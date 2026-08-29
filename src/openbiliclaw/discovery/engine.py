"""Content Discovery Engine.

Coordinates multiple discovery strategies to find content
that matches the user's soul profile.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from collections import Counter, OrderedDict
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from openbiliclaw.discovery.admission import effective_admission_threshold
from openbiliclaw.discovery.eval_payload import (
    CanonicalEvaluationBatch,
    build_canonical_evaluation_batch,
    render_sparse_evaluation_json,
    resolve_local_evaluation_results,
)
from openbiliclaw.discovery.eval_reason import normalize_evaluation_reason
from openbiliclaw.discovery.learned_scorer import LearnedBatchResult, LearnedRelevanceScorer
from openbiliclaw.discovery.prefilter_audit import (
    PREFILTER_EXPLORE_EXEMPT_STATUS,
    PREFILTER_NO_INTERESTS_STATUS,
    PREFILTER_OK_STATUS,
    PrefilterShadowDecision,
    PrefilterShadowOutcome,
    classify_prefilter_context,
    hash_prefilter_candidate_identity,
    is_explicit_strong_interest_context,
    sanitize_prefilter_platform,
)
from openbiliclaw.discovery.strategies._utils import (
    _CONTENT_PROMPT_DOMAIN_CAP,
    _CONTENT_PROMPT_INTEREST_CAP,
    build_profile_summary,
    compact_content_prompt_profile_summary,
)
from openbiliclaw.discovery.style_keys import normalize_style_key
from openbiliclaw.discovery.temporal import (
    TEMPORAL_POLICY_VERSION,
    TemporalEvaluation,
    evaluate_temporal_eligibility,
    ground_temporal_evaluation,
    is_complete_temporal_evidence_marker,
    parse_temporal_evaluation,
    schedule_temporal_evaluation,
)
from openbiliclaw.llm.evaluation_wire import encode_evaluation_row_wire
from openbiliclaw.llm.json_utils import (
    extract_llm_json_list,
    parse_llm_json_tolerant,
    validated_text_field,
)
from openbiliclaw.llm.prompt_cache import (
    PromptLayerRenderCache,
    profile_prompt_layers,
    stable_json_digest,
)
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.saved_sync.identity import (
    canonical_source_platform,
    content_storage_key,
    make_item_key,
)
from openbiliclaw.sources.platforms import normalize_source_platform

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping, Sequence

    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)
_T = TypeVar("_T")
_EvalCacheEntryV4 = tuple[float, str, str, str, str, str, float, str, str]
_EvalCacheEntryV5Legacy = tuple[
    float,
    str,
    str,
    str,
    str,
    str,
    float,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    bool,
]
_EvalCacheEntryV5 = tuple[
    float,
    str,
    str,
    str,
    str,
    str,
    float,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    bool,
]
_EvalCacheEntry = (
    tuple[float, str, str, str]
    | tuple[float, str, str, str, str]
    | _EvalCacheEntryV4
    | _EvalCacheEntryV5Legacy
    | _EvalCacheEntryV5
)
_BILIBILI_CONTENT_ID_PATTERN = re.compile(r"^BV[0-9A-Za-z]+$")
_CANONICAL_STORAGE_KEY_PLATFORMS = frozenset(
    {
        "bilibili",
        "xiaohongshu",
        "douyin",
        "youtube",
        "twitter",
        "zhihu",
        "reddit",
        "bangumi",
        "v2ex",
        "web",
    }
)
_EVALUATE_BATCH_HARD_CAP_DEFAULT: int = 90
_DEFAULT_EVAL_BATCH_SIZE: int = 45
_DEFAULT_EVAL_BATCH_CONCURRENCY: int = 2
_EVAL_CACHE_MAX_ENTRIES: int = 4096
_LLM_EVAL_OVERSAMPLE_FACTOR: int = 2


def _namespaced_storage_identity(value: str) -> tuple[str, str] | None:
    raw_platform, separator, raw_content_id = value.strip().partition(":")
    platform = canonical_source_platform(raw_platform)
    if (
        not separator
        or platform != raw_platform.strip().lower()
        or platform not in _CANONICAL_STORAGE_KEY_PLATFORMS
        or not raw_content_id.strip()
    ):
        return None
    return platform, raw_content_id.strip()


_LLM_EVAL_MIN_WINDOW: int = 6
_RAW_CANDIDATE_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "openbiliclaw_discovery_raw_candidate_mode",
    default=False,
)
_EVAL_BATCH_CACHE_VERSION = "content-eval-v6"
_EMBEDDING_PREFILTER_DEFAULT_MODE = "shadow"
_EMBEDDING_PREFILTER_MODES = {"off", "shadow", "enforce"}
_EVAL_SCORER_DEFAULT = "llm"
_EVAL_SCORER_MODES = frozenset({"llm", "learned"})
_LEARNED_SCORER_REASON = "learned relevance scorer"
_DEFAULT_EVALUATION_CANDIDATE_TRANSPORT = "sparse-json"
_EVALUATION_CANDIDATE_TRANSPORTS = frozenset({"production", "row-wire-v1", "sparse-json"})
_EMBEDDING_PREFILTER_MIN_SIMILARITY = 0.2
_EMBEDDING_PREFILTER_REASON = "embedding 预过滤: 与所有兴趣相似度极低"
_NEGATIVE_EXAMPLES_UNSET = object()
_EVAL_RECALL_POOL_CAP = 256
_EVAL_RECALL_MIN_SIMILARITY = 0.45
compact_evaluation_profile_summary = compact_content_prompt_profile_summary


@dataclass(frozen=True)
class _RelatedInterestRecall:
    related: list[str]
    complete: bool


@dataclass(frozen=True)
class _BatchRelatedInterestRecall:
    related_by_index: dict[int, list[str]]
    complete_indices: frozenset[int]


def discovery_raw_candidate_mode_enabled() -> bool:
    """Return whether the current coroutine should fetch without LLM evaluation."""

    return bool(_RAW_CANDIDATE_MODE.get())


def evaluation_profile_prompt_layers(
    profile_summary: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    """Split eval profile prompt input from most stable to most volatile."""
    return profile_prompt_layers(profile_summary)


def _profile_interest_weight(interest: object) -> float:
    return _coerce_recall_weight(getattr(interest, "weight", 0.0))


def _coerce_recall_weight(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | str):
        try:
            return float(value or 0.0)
        except ValueError:
            return 0.0
    return 0.0


def _profile_interests_by_weight(profile: SoulProfile) -> list[object]:
    preferences = getattr(profile, "preferences", None)
    raw_interests = getattr(preferences, "interests", []) if preferences is not None else []
    if not isinstance(raw_interests, list):
        return []
    return sorted(raw_interests, key=_profile_interest_weight, reverse=True)


def _evaluation_recall_interests(profile: SoulProfile) -> list[dict[str, object]]:
    """Tail interests only: ranks beyond the compact block's top-48.

    Interests inside the compact profile block are already visible to the
    model; recalling them per item would duplicate tokens for nothing. On a
    young profile (<= 48 interests) this list is empty and recall costs zero.
    """
    interests: list[dict[str, object]] = []
    tail = _profile_interests_by_weight(profile)[_CONTENT_PROMPT_INTEREST_CAP:_EVAL_RECALL_POOL_CAP]
    for interest in tail:
        name = str(getattr(interest, "name", "") or "").strip()
        if not name:
            continue
        category = str(getattr(interest, "category", "") or "").strip()
        interests.append(
            {
                "name": name,
                "category": category,
                "weight": _profile_interest_weight(interest),
            }
        )
    return interests


def _evaluation_recall_pool_digest_payload(profile: SoulProfile) -> list[tuple[str, str, float]]:
    return [
        (
            str(interest["name"]),
            str(interest["category"]),
            _coerce_recall_weight(interest["weight"]),
        )
        for interest in _evaluation_recall_interests(profile)
    ]


@dataclass
class DiscoveryConcurrencyController:
    """Shared bounded concurrency for external discovery dependencies."""

    bilibili_request_concurrency: int = 2
    # Cap on simultaneous discovery LLM calls. Sized so a typical init
    # discover (4 strategies × ~8 batches each = ~32 batches) fans out
    # in a single wave rather than queueing behind the cap. Each batch
    # is a max-thinking deepseek call (~60-100s); without enough
    # concurrency we'd spend the full P4 budget waiting on the
    # semaphore (observed 17 min wall on 40 batches at concurrency=8,
    # of which only ~100s was actual LLM compute per batch).
    # deepseek has no effective RPM cap at our request sizes, so the
    # only practical limits are the local event loop overhead and the
    # ``chat_active`` yield (which still works to give interactive
    # dialogue priority).
    llm_evaluation_concurrency: int = 32
    search_budget_total: int = 30
    """Total bilibili search API calls allowed per discovery run.

    The budget is split evenly among strategies that use search
    (search, explore, related_chain) to prevent any single strategy
    from exhausting the IP-level rate limit.
    """
    _search_strategy_count: int = field(init=False, default=3, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(init=False, default=None, repr=False)
    _bilibili_semaphore: asyncio.Semaphore | None = field(init=False, default=None, repr=False)
    _llm_semaphore: asyncio.Semaphore | None = field(init=False, default=None, repr=False)

    @property
    def search_budget_per_strategy(self) -> int:
        """Per-strategy share of the search API budget."""
        return max(1, self.search_budget_total // max(1, self._search_strategy_count))

    def _ensure_loop_bound(self) -> None:
        """Recreate semaphores when the controller is used from a new event loop."""
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self._bilibili_semaphore = asyncio.Semaphore(max(1, self.bilibili_request_concurrency))
        self._llm_semaphore = asyncio.Semaphore(max(1, self.llm_evaluation_concurrency))

    async def run_bilibili(self, awaitable: Awaitable[_T]) -> _T:
        """Run one Bilibili-facing awaitable within the request limit."""
        self._ensure_loop_bound()
        assert self._bilibili_semaphore is not None
        async with self._bilibili_semaphore:
            return await awaitable

    chat_active: bool = False
    llm_throttle_seconds: float = 0.0
    """Minimum delay between consecutive discovery LLM calls.

    Kept at 0 for deepseek, which has no effective RPM cap at our
    request sizes. Raise above 0 when fronting a provider with a
    strict RPM ceiling (e.g. Gemini free tier at 15 RPM). The
    ``chat_active`` flag already yields the lane when a dialogue is
    in progress, so the throttle is no longer needed for chat
    protection on deepseek.
    """

    async def run_llm(self, awaitable: Awaitable[_T]) -> _T:
        """Run one LLM-facing awaitable within the evaluation limit.

        When ``chat_active`` is True (a user dialogue is in progress),
        discovery LLM calls yield until the dialogue finishes.  This
        prevents discovery from saturating the LLM API's RPM quota and
        starving interactive chat requests.
        """
        while self.chat_active:
            await asyncio.sleep(0.5)
        self._ensure_loop_bound()
        assert self._llm_semaphore is not None
        async with self._llm_semaphore:
            result = await awaitable
            # Throttle: space out discovery LLM calls to avoid RPM exhaustion
            if self.llm_throttle_seconds > 0:
                await asyncio.sleep(self.llm_throttle_seconds)
            return result


class SupportsStructuredTask(Protocol):
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
    ) -> object: ...


class SupportsNegativeExemplarStore(Protocol):
    """Storage surface needed by negative-anchor cache invalidation."""

    def get_latest_event_id(self) -> int | None: ...

    def query_events(
        self,
        *,
        satisfaction_modes: frozenset[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


def llm_eval_candidate_limit(limit: int) -> int:
    """Return the pre-LLM candidate window for a requested result limit."""
    safe_limit = max(1, int(limit))
    return min(
        _EVALUATE_BATCH_HARD_CAP_DEFAULT,
        max(_LLM_EVAL_MIN_WINDOW, safe_limit * _LLM_EVAL_OVERSAMPLE_FACTOR),
    )


def trim_candidates_for_llm(
    candidates: Sequence[_T],
    *,
    limit: int,
    source_context: str,
) -> list[_T]:
    """Keep a bounded pre-LLM candidate window while preserving upstream order."""
    eval_limit = llm_eval_candidate_limit(limit)
    if len(candidates) <= eval_limit:
        return list(candidates)
    logger.info(
        "%s: trimming LLM eval candidates from %d to %d (result_limit=%d)",
        source_context,
        len(candidates),
        eval_limit,
        limit,
    )
    return list(candidates[:eval_limit])


def _parse_batch_evaluation_payload(raw: str) -> list[dict[str, Any]] | None:
    """Extract the scored result array from a provider response."""
    payload = extract_llm_json_list(
        raw,
        wrapper_keys=("results", "items", "evaluations", "scores", "data"),
        allow_singleton=True,
        item_predicate=lambda item: "score" in item,
    )
    if payload is None:
        parsed = parse_llm_json_tolerant(raw)
        if isinstance(parsed, dict):
            mapped_payload: list[dict[str, Any]] = []
            for key, value in parsed.items():
                if not isinstance(value, dict) or "score" not in value:
                    continue
                item = dict(value)
                identifier = str(key).strip()
                if identifier:
                    item.setdefault("content_id", identifier)
                    if identifier.startswith("BV"):
                        item.setdefault("bvid", identifier)
                mapped_payload.append(item)
            if mapped_payload:
                return mapped_payload
        return None
    return [dict(item) for item in payload]


def _apply_temporal_evaluation(
    content: DiscoveredContent,
    temporal: TemporalEvaluation,
    *,
    evaluated_at: str,
    evidence_text: str,
) -> None:
    """Ground, schedule, and copy one atomic temporal result onto a candidate."""

    # Current cache tuples already carry the original evaluation/review clocks.
    # Fresh model results do not: ground their explicit evidence against only
    # prompt-visible content, then let the deterministic policy own scheduling.
    if not temporal.temporal_evaluated_at:
        temporal = ground_temporal_evaluation(temporal, content_text=evidence_text)
        temporal = schedule_temporal_evaluation(temporal, evaluated_at=evaluated_at)

    content.temporal_class = temporal.temporal_class
    content.temporal_confidence = temporal.temporal_confidence
    content.temporal_reason = temporal.temporal_reason
    content.temporal_policy_version = temporal.temporal_policy_version
    content.temporal_validity_mode = temporal.temporal_validity_mode
    content.temporal_valid_until = temporal.temporal_valid_until
    content.temporal_scope = temporal.temporal_scope
    content.temporal_evidence = temporal.temporal_evidence
    content.temporal_state = temporal.temporal_state
    content.temporal_next_review_at = temporal.temporal_next_review_at
    content.temporal_evaluated_at = temporal.temporal_evaluated_at
    content.temporal_evidence_complete = temporal.evidence_complete
    # Legacy storage adapters still consume this runtime overwrite marker.
    content.temporal_evaluated = temporal.evidence_complete and temporal.temporal_class != "unknown"


def _decode_eval_cache_entry(
    cached: _EvalCacheEntry,
) -> tuple[float, str, str, str, str, TemporalEvaluation]:
    """Decode v5 evaluator cache tuples and legacy 4/5/9-field entries."""

    score, reason, topic_group, style_key = cached[:4]
    franchise_key = cached[4] if len(cached) >= 5 else ""
    temporal = TemporalEvaluation()
    if len(cached) >= 17:
        cached_v5 = cast("_EvalCacheEntryV5", cached)
        temporal = TemporalEvaluation(
            temporal_class=cached_v5[5],
            temporal_confidence=cached_v5[6],
            temporal_reason=cached_v5[7],
            temporal_policy_version=cached_v5[8],
            temporal_validity_mode=cached_v5[9],
            temporal_valid_until=cached_v5[10],
            temporal_scope=cached_v5[11],
            temporal_evidence=cached_v5[12],
            temporal_state=cached_v5[13],
            temporal_next_review_at=cached_v5[14],
            temporal_evaluated_at=cached_v5[15],
            evidence_complete=cached_v5[16],
        )
    elif len(cached) >= 16:
        cached_v5_legacy = cast("_EvalCacheEntryV5Legacy", cached)
        temporal = TemporalEvaluation(
            temporal_class=cached_v5_legacy[5],
            temporal_confidence=cached_v5_legacy[6],
            temporal_reason=cached_v5_legacy[7],
            temporal_policy_version=cached_v5_legacy[8],
            temporal_validity_mode=cached_v5_legacy[9],
            temporal_valid_until=cached_v5_legacy[10],
            temporal_scope=cached_v5_legacy[11],
            temporal_evidence=cached_v5_legacy[12],
            temporal_next_review_at=cached_v5_legacy[13],
            temporal_evaluated_at=cached_v5_legacy[14],
            evidence_complete=cached_v5_legacy[15],
        )
    elif len(cached) >= 9:
        cached_v4 = cast("_EvalCacheEntryV4", cached)
        temporal = TemporalEvaluation(
            temporal_class=cached_v4[5],
            temporal_confidence=cached_v4[6],
            temporal_reason=cached_v4[7],
            temporal_policy_version=cached_v4[8],
        )
    return score, reason, topic_group, style_key, franchise_key, temporal


def _eval_cache_entry_for_content(
    content: DiscoveredContent,
) -> _EvalCacheEntryV5:
    """Build the v5 in-memory cache shape from an evaluated candidate."""

    return (
        content.relevance_score,
        content.relevance_reason,
        content.topic_group,
        content.style_key,
        content.franchise_key,
        content.temporal_class,
        content.temporal_confidence,
        content.temporal_reason,
        content.temporal_policy_version,
        content.temporal_validity_mode,
        content.temporal_valid_until,
        content.temporal_scope,
        content.temporal_evidence,
        content.temporal_state,
        content.temporal_next_review_at,
        content.temporal_evaluated_at,
        content.temporal_evidence_complete,
    )


def _temporal_evidence_text(content: DiscoveredContent) -> str:
    """Return only candidate text that was eligible to ground model evidence."""

    return "\n".join(
        value
        for value in (
            content.title,
            content.description,
            content.body_text,
            content.published_label,
        )
        if value
    )


def _temporal_evidence_text_from_prompt_item(item: Mapping[str, object]) -> str:
    """Return exactly the text fields rendered for one evaluator item.

    Batch transports may truncate or omit fields. Grounding against this
    projection prevents text outside the actual model request from upgrading
    a state claim into a hard eligibility decision.
    """

    return "\n".join(
        value
        for field_name in ("title", "description", "body_text", "published_label")
        if isinstance((value := item.get(field_name)), str) and value
    )


def _content_result_keys(content: DiscoveredContent) -> set[str]:
    """Stable keys that may identify a content item in batched LLM results."""
    return {
        key
        for key in {
            str(getattr(content, "bvid", "") or "").strip(),
            str(getattr(content, "content_id", "") or "").strip(),
        }
        if key
    }


_PROMPT_VISIBLE_METRIC_FIELDS: tuple[str, ...] = (
    "view_count",
    "like_count",
    "favorite_count",
    "collect_count",
    "comment_count",
    "share_count",
    "danmaku_count",
    "reply_count",
    "retweet_count",
    "bookmark_count",
)


def _prompt_visible_content_fields(content: DiscoveredContent) -> dict[str, object]:
    fields: dict[str, object] = {
        field_name: int(getattr(content, field_name, 0) or 0)
        for field_name in _PROMPT_VISIBLE_METRIC_FIELDS
    }
    fields["tags"] = list(getattr(content, "tags", []) or [])
    rating_score = float(getattr(content, "rating_score", 0.0) or 0.0)
    rating_count = int(getattr(content, "rating_count", 0) or 0)
    source_rank = int(getattr(content, "source_rank", 0) or 0)
    if rating_score > 0:
        fields["rating_score"] = rating_score
    if rating_count > 0:
        fields["rating_count"] = rating_count
    if source_rank > 0:
        fields["source_rank"] = source_rank
    return fields


def _normalize_prompt_text_for_dedupe(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def _prompt_description_for_content(
    content: DiscoveredContent,
    *,
    limit: int | None = None,
) -> str:
    description = str(content.description or "")
    if limit is not None:
        description = description[:limit]
    desc_key = _normalize_prompt_text_for_dedupe(description)
    body_key = _normalize_prompt_text_for_dedupe(str(content.body_text or ""))
    if desc_key and body_key.startswith(desc_key):
        return ""
    return description


def _single_evaluation_content_summary(content: DiscoveredContent) -> dict[str, object]:
    """Build the exact content object rendered by the single evaluator."""

    return {
        "content_id": content.content_id or content.bvid,
        "content_url": content.content_url,
        "source_platform": content.source_platform or "bilibili",
        "content_type": content.content_type,
        "body_text": content.body_text,
        "title": content.title,
        "up_name": content.up_name,
        "author_name": content.author_name or content.up_name,
        "description": _prompt_description_for_content(content),
        "published_at": content.published_at,
        "published_label": content.published_label,
        "duration": content.duration,
        "source_strategy": content.source_strategy,
        **_prompt_visible_content_fields(content),
    }


def _batch_evaluation_platform(content: DiscoveredContent) -> str:
    platform = normalize_source_platform(
        content.source_platform,
        default="bilibili" if content.bvid else "",
    )
    return platform or "bilibili"


def _batch_evaluation_content_type(content: DiscoveredContent) -> str:
    from openbiliclaw.discovery.candidate_pool import resolve_content_type

    platform = _batch_evaluation_platform(content)
    return resolve_content_type(content.content_type, platform)


def _batch_evaluation_content_item(
    content: DiscoveredContent,
    *,
    source_context: str,
) -> dict[str, object]:
    """Build the exact text content object rendered by the batch evaluator."""

    platform = _batch_evaluation_platform(content)
    return {
        "bvid": content.bvid,
        "content_id": content.content_id or content.bvid,
        "content_url": content.content_url,
        "source_platform": platform,
        "source_strategy": content.source_strategy,
        "source_context": source_context or content.source_strategy,
        "content_type": _batch_evaluation_content_type(content),
        "body_text": content.body_text,
        "title": content.title,
        "up_name": content.up_name,
        "author_name": content.author_name or content.up_name,
        "description": _prompt_description_for_content(content, limit=400),
        "published_at": content.published_at,
        "published_label": content.published_label,
        "cover_url": content.cover_url,
        "duration": content.duration,
        **_prompt_visible_content_fields(content),
    }


def _batch_results_by_content_key(
    payload: list[dict[str, Any]],
    batch: list[DiscoveredContent],
) -> dict[str, dict[str, Any]] | None:
    """Return payload entries keyed by content ID when the LLM supplied IDs."""
    valid_keys: set[str] = set()
    for content in batch:
        valid_keys.update(_content_result_keys(content))

    matched: dict[str, dict[str, Any]] = {}
    duplicated_keys: set[str] = set()
    saw_identifier = False
    for item in payload:
        raw_key = str(item.get("bvid") or item.get("content_id") or "").strip()
        if not raw_key:
            continue
        saw_identifier = True
        if raw_key not in valid_keys:
            continue
        if raw_key in duplicated_keys:
            continue
        if raw_key in matched:
            matched.pop(raw_key, None)
            duplicated_keys.add(raw_key)
            continue
        matched[raw_key] = item

    return matched if saw_identifier else None


@dataclass
class DiscoveredContent:
    """A piece of content discovered by the engine."""

    bvid: str = ""  # Bilibili video ID (legacy; prefer content_id for new code)
    title: str = ""
    up_name: str = ""  # UP主 name (legacy; prefer author_name for new code)
    up_mid: int = 0  # UP主 ID
    cover_url: str = ""
    duration: int = 0  # seconds
    view_count: int = 0
    like_count: int = 0
    favorite_count: int = 0
    collect_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    danmaku_count: int = 0
    reply_count: int = 0
    retweet_count: int = 0
    bookmark_count: int = 0
    # Metrics listed here were actually present in the upstream envelope.
    # A zero count without this evidence means structurally unavailable, not
    # an observed aggregate of zero.
    engagement_available: list[str] = field(default_factory=list)
    # Catalog metrics (Bangumi and future catalog-style sources). These are
    # deliberately separate from engagement counts: a 1–10 rating is not a
    # like, and rating participants are not comments.
    rating_score: float = 0.0
    rating_count: int = 0
    source_rank: int = 0
    tags: list[str] = field(default_factory=list)
    topic_key: str = ""
    topic_group: str = ""  # Coarse semantic category (e.g. "强化学习") for diversity
    style_key: str = ""
    # Franchise / IP / series key tagged by the LLM at evaluation time
    # (e.g. "原神", "崩坏:星穹铁道", "ChatGPT", "塞尔达传说"). Empty
    # for general-interest content. Lets the curator down-rank items
    # in the same IP after a single dislike, and lets the
    # ``/api/recommendations`` endpoint cap how many same-franchise
    # items appear in a single response window. Better than the
    # heuristic title-substring approach (which v0.3.17 briefly tried)
    # because the LLM already saw title + description + topic and can
    # infer the IP correctly even when the title is bilingual or coded
    # ("提瓦特摄影" → 原神, "宝可梦" → 精灵宝可梦, etc.).
    franchise_key: str = ""
    description: str = ""
    published_at: str = ""
    published_label: str = ""
    source_strategy: str = ""  # Which strategy found this
    # Retrieval-only provenance (for example ``recent``). It can refine the
    # discovery-candidate ``source_context`` without changing source strategy,
    # relevance, admission thresholds, or recommendation source-fatigue rules.
    discovery_lane: str = ""
    relevance_score: float = 0.0  # 0.0 - 1.0 (based on user soul)
    relevance_reason: str = ""  # Why this is relevant to the user
    temporal_class: str = "unknown"  # Why this content's value may expire
    temporal_confidence: float = 0.0  # Evaluator confidence in temporal_class
    temporal_reason: str = ""  # Short diagnostic for the temporal classification
    temporal_policy_version: str = TEMPORAL_POLICY_VERSION  # Code-owned policy schema
    temporal_validity_mode: str = "none"
    temporal_valid_until: str = ""
    temporal_scope: str = "none"
    temporal_evidence: str = ""
    temporal_state: str = "unknown"
    temporal_next_review_at: str = ""  # Deterministically scheduled, never model-owned
    temporal_evaluated_at: str = ""  # Exact UTC clock used for this evaluation
    temporal_evidence_complete: bool = False
    # True only for complete, non-neutral evaluator evidence that may replace
    # a durable classification. Raw/default/unknown results stay false so a
    # re-ingest cannot wash a high-confidence hard-gate decision to unknown.
    temporal_evaluated: bool = False
    pool_expression: str = ""  # Precomputed recommendation copy for fast popup paths
    pool_topic_label: str = ""  # Precomputed personalized topic label for fast popup paths
    candidate_tier: str = "primary"  # Primary discovery vs backfill supply
    discovered_at: str = ""  # Cache lifecycle timestamp; never publication time
    last_scored_at: str = ""  # Evaluation lifecycle timestamp; never publication time

    # ── Multi-source fields (Phase 0) ───────────────────────────────
    content_id: str = ""  # Universal content ID; equals bvid for Bilibili content
    item_key: str = field(init=False, default="")  # Canonical platform-qualified identity
    content_url: str = ""  # Direct clickable URL
    source_platform: str = ""  # "bilibili" | "xiaohongshu" | "web" | ...
    author_name: str = ""  # Universal author name; equals up_name for Bilibili
    score_threshold: float = 0.0  # Strategy-specific admission floor for raw candidates
    body_text: str = ""  # tweet/thread full text; empty for video sources
    content_type: str = "video"  # shape: "video" | "note" | "tweet" | "thread"
    # P1.8 yield provenance: the ``discovery_keywords.id`` of the search word
    # that produced this item (unified keyword planner). ``None`` for every
    # non-search / legacy / flag-off path — the admit-time yield backfill is a
    # no-op then, so attribution stays opt-in and byte-compatible.
    source_keyword_id: int | None = None

    def __post_init__(self) -> None:
        storage_identity = _namespaced_storage_identity(self.bvid) if self.bvid else None
        if not self.content_id and self.bvid:
            if storage_identity is not None and (
                not self.source_platform
                or canonical_source_platform(self.source_platform) == storage_identity[0]
            ):
                self.content_id = storage_identity[1]
            else:
                self.content_id = self.bvid
        if not self.source_platform and self.bvid:
            self.source_platform = (
                storage_identity[0] if storage_identity is not None else "bilibili"
            )
        if not self.author_name and self.up_name:
            self.author_name = self.up_name
        if (
            not self.content_url
            and canonical_source_platform(self.source_platform) == "bilibili"
            and _BILIBILI_CONTENT_ID_PATTERN.fullmatch(self.content_id.strip()) is not None
        ):
            self.content_url = f"https://www.bilibili.com/video/{self.content_id.strip()}"
        if self.source_platform and (self.content_id or self.content_url):
            self.item_key = make_item_key(
                self.source_platform,
                self.content_id,
                self.content_url,
            )

    def to_cache_kwargs(self) -> dict[str, object]:
        """Build the kwargs dict for ``Database.cache_content()``.

        Single source of truth for the DiscoveredContent → content_cache
        field mapping.  Used by discovery's ``_cache_results`` and the
        recommendation engine's ``classify_pool_backlog`` persist loop.
        """
        return {
            "title": self.title,
            "up_name": self.up_name,
            "up_mid": self.up_mid,
            "duration": self.duration,
            "tags": self.tags,
            "topic_key": self.topic_key,
            "topic_group": self.topic_group,
            "style_key": self.style_key,
            "franchise_key": self.franchise_key,
            "description": self.description,
            "published_at": self.published_at,
            "published_label": self.published_label,
            "cover_url": self.cover_url,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "favorite_count": self.favorite_count,
            "collect_count": self.collect_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "danmaku_count": self.danmaku_count,
            "reply_count": self.reply_count,
            "retweet_count": self.retweet_count,
            "bookmark_count": self.bookmark_count,
            "rating_score": self.rating_score,
            "rating_count": self.rating_count,
            "source_rank": self.source_rank,
            "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason,
            "temporal_class": self.temporal_class,
            "temporal_confidence": self.temporal_confidence,
            "temporal_reason": self.temporal_reason,
            "temporal_policy_version": self.temporal_policy_version,
            "temporal_validity_mode": self.temporal_validity_mode,
            "temporal_valid_until": self.temporal_valid_until,
            "temporal_scope": self.temporal_scope,
            "temporal_evidence": self.temporal_evidence,
            "temporal_state": self.temporal_state,
            "temporal_next_review_at": self.temporal_next_review_at,
            "temporal_evaluated_at": self.temporal_evaluated_at,
            "temporal_evidence_complete": self.temporal_evidence_complete,
            "temporal_evaluated": self.temporal_evaluated,
            "candidate_tier": self.candidate_tier,
            "source": self.source_strategy,
            "item_key": self.item_key,
            "source_platform": self.source_platform or "bilibili",
            "content_id": self.content_id or self.bvid,
            "content_url": self.content_url,
            "author_name": self.author_name or self.up_name,
            "body_text": self.body_text,
            "content_type": self.content_type,
            "source_keyword_id": self.source_keyword_id,
        }


@dataclass(frozen=True)
class CacheEvaluatedItemOutcome:
    """Authoritative cache-admission outcome for one evaluated item."""

    bvid: str
    admitted: bool
    # ``None`` means a legacy database adapter did not expose a lock-held
    # result, so the batch falls back to its historical row-count delta.
    newly_cached: bool | None = None
    temporal_rejection_reason: str = ""
    temporal_review_reason: str = ""


@dataclass(frozen=True)
class CacheEvaluatedBatchOutcome:
    """Detailed admission result used by the durable candidate state machine."""

    newly_cached: int = 0
    items: tuple[CacheEvaluatedItemOutcome, ...] = ()


# v0.3.50+: per-batch franchise cap for ``_evaluate_batch``. The LLM
# correctly identifies when a batch has many same-IP items (the prompt
# mandates batch-wide franchise consistency), but pre-v0.3.50 we kept
# them all and let serve()'s diversifier sort it out — by which point
# the pool was already franchise-skewed. Cap=4 lets a series have a
# small foothold in each refresh round but stops a single ``related_chain``
# excursion from dumping 13 items of the same UP into one batch.
_BATCH_FRANCHISE_CAP: int = 4

# v0.3.51+: per-batch style cap. Mirrors the franchise cap above —
# without it, a single eval_batch easily had 9-12 items of the same
# style (mood_release / story_immersion / social_chat / hands_on all
# observed at 30-40% concentration in production). 8/30 = ~27% which
# still lets a dominant style breathe but blocks single-style
# domination of the pool.
_BATCH_STYLE_CAP: int = 8

# v0.3.50+: pool-wide franchise quota for ``_cache_results``. Once a
# franchise has this many items in the pool, new same-franchise items
# are skipped before they can compete for serve() slots. Sized at ~1.5%
# of the default pool target (600), so 9-10 items is enough breathing
# room for a series the user actively follows but not enough to skew
# the whole pool's tone.
_POOL_FRANCHISE_QUOTA: int = 10

# v0.3.50+: per-UP cap inside a single related_chain depth round.
# Without this, related_chain following a single seed could fan out
# into 13+ items of the same UP (张雪机车 was the production trigger).
_RELATED_CHAIN_PER_UP_CAP: int = 3


class DiscoveryStrategy(ABC):
    """Base class for content discovery strategies."""

    @property
    def source_platform(self) -> str:
        """Canonical platform produced by this strategy.

        Bilibili is the historical/default strategy family. Multi-source
        strategies override this so cache backfill can never leak candidates
        from another platform into a source-scoped discovery run.
        """
        return "bilibili"

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        ...

    def filter_candidates_for_eval(
        self,
        candidates: list[DiscoveredContent],
        *,
        now: Any | None = None,
    ) -> list[DiscoveredContent]:
        """Apply this source's date preference before LLM evaluation.

        Out-of-window candidates are removed regardless of ``weight`` so the
        evaluator never spends tokens on content the user explicitly excluded.
        """

        preference = getattr(self, "date_preference", None) or getattr(
            self, "publication_preference", None
        )
        if preference is None:
            return candidates
        from datetime import UTC, datetime

        from openbiliclaw.recommendation.publication_preference import (
            PRESET_ALL,
            evaluate_source_publication_preference,
        )

        if getattr(preference, "preset", PRESET_ALL) == PRESET_ALL:
            return candidates

        current = (now or datetime.now(UTC)).astimezone(UTC)
        return [
            item
            for item in candidates
            if evaluate_source_publication_preference(
                published_at=getattr(item, "published_at", ""),
                preference=preference,
                now=current,
            ).in_range
        ]

    @abstractmethod
    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        """Execute the discovery strategy.

        Args:
            profile: Current user soul profile for relevance guidance.
            limit: Maximum number of items to return.

        Returns:
            List of discovered content items.
        """
        ...

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        """Return an expanded/relaxed variant for supply backfill if supported."""
        return None


def _strategy_declares_param(fn: Any, name: str) -> bool:
    """Return whether a strategy discover callable declares an explicit ``name`` param."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return name in signature.parameters


def _strategy_accepts_kwarg(fn: Any, name: str) -> bool:
    """Return whether a strategy discover callable accepts a keyword ``name``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return name in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _injected_keyword_kwarg(fn: Any) -> str | None:
    """Return the kwarg name to forward unified-planner injected words under.

    The real search sub-strategies (B站 ``SearchStrategy``, ``XSearchStrategy``,
    ``YoutubeSearchStrategy``) all read an explicit ``queries`` parameter — NOT
    ``keywords`` — so the engine must forward injected words under whatever name
    the strategy actually declares, or the injection is a silent no-op (the word
    is claimed + marked ``used`` while the search never sees it). Preference:

    1. an explicit ``queries`` param (every real search strategy),
    2. an explicit ``keywords`` param (older/alternate signatures + fakes),
    3. ``keywords`` when the callable only declares ``**kwargs`` (legacy contract),
    4. ``None`` → the strategy takes no injected words (non-search sub-strategies).
    """
    if _strategy_declares_param(fn, "queries"):
        return "queries"
    if _strategy_declares_param(fn, "keywords"):
        return "keywords"
    if _strategy_accepts_kwarg(fn, "keywords"):
        return "keywords"
    return None


def _strategy_accepts_pool_snapshot(fn: Any) -> bool:
    """Return whether a strategy discover callable accepts ``pool_snapshot=``."""
    return _strategy_accepts_kwarg(fn, "pool_snapshot")


def _strategy_accepts_keyword_ids(fn: Any) -> bool:
    """Return whether a strategy discover callable declares ``keyword_ids=``.

    P1.8 yield provenance: search sub-strategies that opt in declare an explicit
    ``keyword_ids`` parameter (a ``keyword text → discovery_keywords.id`` map)
    and stamp each produced item's ``source_keyword_id`` inside their per-keyword
    loop. We forward the map ONLY to callables that declare it explicitly — never
    via ``**kwargs`` — so non-search strategies + fakes stay byte-identical.
    """
    return _strategy_declares_param(fn, "keyword_ids")


async def _call_strategy_discover(
    strategy: DiscoveryStrategy,
    profile: SoulProfile,
    *,
    limit: int,
    pool_snapshot: Any | None,
    keywords: list[str] | None = None,
    keyword_ids: dict[str, int] | None = None,
) -> list[DiscoveredContent]:
    discover_fn: Any = strategy.discover
    kwargs: dict[str, Any] = {"limit": limit}
    if _strategy_accepts_pool_snapshot(discover_fn):
        kwargs["pool_snapshot"] = pool_snapshot
    # Only forward injected keywords when the caller supplied them AND the
    # strategy actually accepts them — under the name the strategy declares
    # (real search strategies read ``queries``, not ``keywords``). Non-search
    # sub-strategies declare neither, so they are left byte-identical.
    if keywords is not None:
        inject_kwarg = _injected_keyword_kwarg(discover_fn)
        if inject_kwarg is not None:
            kwargs[inject_kwarg] = keywords
    # P1.8: forward the parallel keyword→id map for yield attribution, but only
    # to a strategy that explicitly opted in (declares ``keyword_ids``). Flag-off
    # / non-injected callers pass ``None`` → never forwarded → no stamping.
    if keyword_ids and _strategy_accepts_keyword_ids(discover_fn):
        kwargs["keyword_ids"] = keyword_ids
    return cast("list[DiscoveredContent]", await discover_fn(profile, **kwargs))


class ContentDiscoveryEngine:
    """Orchestrates multiple discovery strategies.

    Available strategies:
    - Search: keyword-based search from user interests
    - Related: follow related recommendation chains
    - Trending: scan trending/ranking content
    - Comments: mine recommendations from comment sections
    - UPTrack: track followed/discovered UP主
    - Explore: cross-domain surprise discovery
    """

    def __init__(
        self,
        llm_service: SupportsStructuredTask | None = None,
        database: Database | None = None,
        *,
        concurrency: DiscoveryConcurrencyController | None = None,
        embedding_service: SupportsEmbeddingService | None = None,
        target_primary_count: int = 20,
        backfill_target_count: int = 40,
        multimodal_evaluation_enabled: bool = False,
        multimodal_batch_size: int = 8,
        multimodal_image_max_px: int = 384,
        multimodal_image_quality: int = 72,
        multimodal_image_timeout_seconds: int = 6,
        multimodal_vision_supported: bool | None = None,
        eval_batch_concurrency: int = _DEFAULT_EVAL_BATCH_CONCURRENCY,
        eval_prefilter_mode: str = _EMBEDDING_PREFILTER_DEFAULT_MODE,
        eval_scorer: str = _EVAL_SCORER_DEFAULT,
        learned_scorer: LearnedRelevanceScorer | None = None,
        compact_evaluation_json: bool = False,
        evaluation_candidate_transport: str = _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT,
    ) -> None:
        self._strategies: list[DiscoveryStrategy] = []
        self._llm_service = llm_service
        self._database = database
        self._concurrency = concurrency
        self._embedding_service = embedding_service
        self._target_primary_count = max(1, target_primary_count)
        self._backfill_target_count = max(self._target_primary_count, backfill_target_count)
        self.multimodal_evaluation_enabled = bool(multimodal_evaluation_enabled)
        self.multimodal_batch_size = max(1, min(12, int(multimodal_batch_size)))
        self.multimodal_image_max_px = max(128, min(768, int(multimodal_image_max_px)))
        self.multimodal_image_quality = max(40, min(90, int(multimodal_image_quality)))
        self.multimodal_image_timeout_seconds = max(
            1,
            min(20, int(multimodal_image_timeout_seconds)),
        )
        self.eval_batch_concurrency = max(1, min(16, int(eval_batch_concurrency)))
        self.eval_prefilter_mode = self._normalize_eval_prefilter_mode(eval_prefilter_mode)
        self._eval_scorer = self._normalize_eval_scorer(eval_scorer)
        if self._eval_scorer == "learned" and learned_scorer is None:
            learned_scorer = LearnedRelevanceScorer(embedding_service=embedding_service)
        self._learned_scorer = learned_scorer
        # Replay-only unless and until the real provider quality/token gate
        # approves compact deterministic evaluator JSON.
        self.compact_evaluation_json = bool(compact_evaluation_json)
        normalized_candidate_transport = str(evaluation_candidate_transport).strip().lower()
        if normalized_candidate_transport not in _EVALUATION_CANDIDATE_TRANSPORTS:
            supported = ", ".join(sorted(_EVALUATION_CANDIDATE_TRANSPORTS))
            raise ValueError(
                "unsupported evaluation candidate transport "
                f"{evaluation_candidate_transport!r}; expected one of: {supported}"
            )
        # Sparse JSON is the production batch-candidate contract. Explicit
        # ``production`` retains the historical pretty-JSON/global-ID rollback
        # path; ``row-wire-v1`` remains an internal replay-only seam.
        self.evaluation_candidate_transport = normalized_candidate_transport
        self._multimodal_vision_supported_override = multimodal_vision_supported
        self.multimodal_unavailable_reason = ""
        self._eval_cache: OrderedDict[str, _EvalCacheEntry] = OrderedDict()
        self._evaluation_profile_prompt_cache = PromptLayerRenderCache()
        # v0.3.x negative-anchors cache: (timestamp, latest_event_id,
        # exemplars). Refreshes when either the latest event id changes
        # (new negative classified) or 5 minutes have elapsed.
        self._negative_exemplars_cache: tuple[float, int | None, list[dict[str, object]]] | None = (
            None
        )

    def _eval_cache_store(self) -> OrderedDict[str, _EvalCacheEntry]:
        # Tests (and older call sites) reset the cache by assigning a plain
        # dict; convert in place so LRU bookkeeping never AttributeErrors.
        cache = self._eval_cache
        if not isinstance(cache, OrderedDict):
            cache = OrderedDict(cache)
            self._eval_cache = cache
        return cache

    def _get_eval_cache_entry(self, cache_key: str) -> _EvalCacheEntry | None:
        cache = self._eval_cache_store()
        cached = cache.get(cache_key)
        if cached is None:
            return None
        cache.move_to_end(cache_key)
        return cached

    def _set_eval_cache_entry(self, cache_key: str, entry: _EvalCacheEntry) -> None:
        cache = self._eval_cache_store()
        cache[cache_key] = entry
        cache.move_to_end(cache_key)
        while len(cache) > _EVAL_CACHE_MAX_ENTRIES:
            cache.popitem(last=False)

    @staticmethod
    def _normalize_eval_prefilter_mode(mode: str) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in _EMBEDDING_PREFILTER_MODES:
            return normalized
        return _EMBEDDING_PREFILTER_DEFAULT_MODE

    @staticmethod
    def _normalize_eval_scorer(scorer: str) -> str:
        normalized = str(scorer or "").strip().lower()
        if normalized in _EVAL_SCORER_MODES:
            return normalized
        return _EVAL_SCORER_DEFAULT

    @staticmethod
    def _embedding_prefilter_content_text(content: DiscoveredContent) -> str:
        return f"{content.title} {content.description or ''}".strip()

    def _embedding_prefilter_interest_labels(self, profile: SoulProfile) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()

        def append_label(value: object) -> None:
            label = str(value or "").strip()
            if not label or label in seen:
                return
            labels.append(label)
            seen.add(label)

        ranked_interests = sorted(
            profile.preferences.interests,
            key=_profile_interest_weight,
            reverse=True,
        )
        # Enforce must see every interest that the downstream long-tail recall
        # can surface. Restricting this to the compact prompt's visible block
        # would reject candidates matching ranks 49..256 before recall ran.
        for interest in ranked_interests[:_EVAL_RECALL_POOL_CAP]:
            append_label(interest.name)

        compact_profile = compact_evaluation_profile_summary(
            self._evaluation_profile_summary(profile)
        )
        raw_domains = compact_profile.get("interest_domains")
        if isinstance(raw_domains, list):
            for domain in raw_domains[:_CONTENT_PROMPT_DOMAIN_CAP]:
                if isinstance(domain, dict):
                    append_label(domain.get("domain"))
                else:
                    append_label(domain)

        return labels

    async def _embedding_prefilter(
        self,
        contents: Sequence[DiscoveredContent],
        profile: SoulProfile,
    ) -> dict[int, float]:
        """Return candidate indexes that are too dissimilar for LLM evaluation."""

        try:
            filtered, _decisions = await self._embedding_prefilter_analysis(
                contents,
                profile,
                audit=False,
            )
        except Exception:
            logger.warning("embedding prefilter failed unexpectedly; failing open", exc_info=True)
            return {}
        return filtered

    async def _embedding_prefilter_shadow_analysis(
        self,
        contents: Sequence[DiscoveredContent],
        profile: SoulProfile,
        *,
        source_context: str,
        profile_digest: str,
    ) -> tuple[dict[int, float], list[PrefilterShadowDecision]]:
        """Return would-filter scores plus privacy-safe shadow decisions."""

        try:
            return await self._embedding_prefilter_analysis(
                contents,
                profile,
                audit=True,
                source_context=source_context,
                profile_digest=profile_digest,
            )
        except Exception:
            logger.warning(
                "embedding prefilter shadow analysis failed unexpectedly; failing open",
                exc_info=True,
            )
            return {}, []

    async def _embedding_prefilter_analysis(
        self,
        contents: Sequence[DiscoveredContent],
        profile: SoulProfile,
        *,
        audit: bool,
        source_context: str = "",
        profile_digest: str = "",
    ) -> tuple[dict[int, float], list[PrefilterShadowDecision]]:
        """Compute prefilter decisions while failing open on degraded vectors."""

        decisions: list[PrefilterShadowDecision] = []
        if not contents:
            return {}, decisions

        embedding_namespace = self._evaluation_embedding_namespace() if audit else ""

        def record(
            index: int,
            *,
            similarity: float | None,
            would_filter: bool,
            status: str,
            fail_open: bool,
        ) -> None:
            if not audit:
                return
            content = contents[index]
            context_class = classify_prefilter_context(
                source_context,
                content.source_strategy,
            )
            platform = normalize_source_platform(
                content.source_platform,
                default="bilibili" if content.bvid else "unknown",
            )
            decisions.append(
                PrefilterShadowDecision(
                    content_index=index,
                    candidate_hash=hash_prefilter_candidate_identity(
                        self._content_identity(content)
                    ),
                    platform_class=sanitize_prefilter_platform(platform),
                    context_class=context_class,
                    similarity=similarity,
                    threshold=_EMBEDDING_PREFILTER_MIN_SIMILARITY,
                    explore=(context_class == "explore"),
                    embedding_namespace=embedding_namespace,
                    profile_digest=profile_digest,
                    would_filter=would_filter,
                    embedding_status=status,
                    fail_open=fail_open,
                    explicit_strong_interest=is_explicit_strong_interest_context(
                        context_class=context_class,
                        source_keyword_id=content.source_keyword_id,
                    ),
                )
            )

        def record_batch_failure(
            status: str,
        ) -> tuple[dict[int, float], list[PrefilterShadowDecision]]:
            for index, content in enumerate(contents):
                context_class = classify_prefilter_context(
                    source_context,
                    content.source_strategy,
                )
                if context_class == "explore":
                    record(
                        index,
                        similarity=None,
                        would_filter=False,
                        status=PREFILTER_EXPLORE_EXEMPT_STATUS,
                        fail_open=False,
                    )
                else:
                    record(
                        index,
                        similarity=None,
                        would_filter=False,
                        status=status,
                        fail_open=True,
                    )
            return {}, decisions

        # getattr: e2e tests build engines via __new__ without __init__.
        embedding_service = getattr(self, "_embedding_service", None)
        if embedding_service is None:
            return record_batch_failure("embedding_service_missing")

        preferences = getattr(profile, "preferences", None)
        interests = getattr(preferences, "interests", None)
        if not interests:
            return record_batch_failure(PREFILTER_NO_INTERESTS_STATUS)
        if not any(
            classify_prefilter_context(source_context, content.source_strategy) != "explore"
            for content in contents
        ):
            return record_batch_failure(PREFILTER_EXPLORE_EXEMPT_STATUS)

        try:
            labels = self._embedding_prefilter_interest_labels(profile)
        except Exception:
            logger.warning(
                "embedding prefilter: interest labels unavailable; failing open", exc_info=True
            )
            return record_batch_failure("interest_embedding_invalid")
        if not labels:
            return record_batch_failure("interest_embedding_missing")

        from openbiliclaw.llm.embedding import cosine_similarity

        ranked_required_interests = sorted(
            interests,
            key=_profile_interest_weight,
            reverse=True,
        )
        required_labels = {
            str(getattr(interest, "name", "") or "").strip()
            for interest in ranked_required_interests[:_EVAL_RECALL_POOL_CAP]
            if str(getattr(interest, "name", "") or "").strip()
        }

        def valid_vector(
            raw_vector: object,
            *,
            expected_dimension: int | None = None,
        ) -> list[float] | None:
            if not isinstance(raw_vector, list) or not raw_vector:
                return None
            vector: list[float] = []
            for value in raw_vector:
                if isinstance(value, bool) or not isinstance(value, int | float):
                    return None
                number = float(value)
                if not math.isfinite(number):
                    return None
                vector.append(number)
            if expected_dimension is not None and len(vector) != expected_dimension:
                return None
            if not any(value != 0.0 for value in vector):
                return None
            return vector

        interest_vectors: list[list[float]] = []
        expected_dimension: int | None = None
        for label in labels:
            try:
                raw_vector = await embedding_service.embed(label)
            except Exception:
                logger.warning(
                    "embedding prefilter: interest embed failed; failing batch open",
                    exc_info=True,
                )
                return record_batch_failure("interest_embedding_error")
            if not raw_vector:
                if label in required_labels:
                    logger.warning(
                        "embedding prefilter: required interest embed missing; failing batch open"
                    )
                    return record_batch_failure("interest_embedding_missing")
                # Compact domain labels supplement the canonical interest
                # list. Some lightweight/test services do not materialize
                # every derived label; missing an optional supplement is safe
                # only while every canonical interest remains represented.
                logger.debug("embedding prefilter: optional domain embed missing")
                continue
            vector = valid_vector(raw_vector, expected_dimension=expected_dimension)
            if vector is None:
                logger.warning("embedding prefilter: interest vector invalid; failing batch open")
                return record_batch_failure("interest_embedding_invalid")
            expected_dimension = expected_dimension or len(vector)
            interest_vectors.append(vector)

        if not interest_vectors or expected_dimension is None:
            return record_batch_failure("interest_embedding_missing")

        filtered_scores: dict[int, float] = {}
        for index, content in enumerate(contents):
            context_class = classify_prefilter_context(source_context, content.source_strategy)
            if context_class == "explore":
                record(
                    index,
                    similarity=None,
                    would_filter=False,
                    status=PREFILTER_EXPLORE_EXEMPT_STATUS,
                    fail_open=False,
                )
                continue
            content_text = self._embedding_prefilter_content_text(content)
            if not content_text:
                record(
                    index,
                    similarity=None,
                    would_filter=False,
                    status="content_text_missing",
                    fail_open=True,
                )
                continue
            try:
                raw_content_vector = await embedding_service.embed(content_text)
            except Exception:
                logger.debug(
                    "embedding prefilter: content embed failed for %s",
                    content.content_id or content.bvid or content.title,
                    exc_info=True,
                )
                record(
                    index,
                    similarity=None,
                    would_filter=False,
                    status="content_embedding_error",
                    fail_open=True,
                )
                continue
            if not raw_content_vector:
                record(
                    index,
                    similarity=None,
                    would_filter=False,
                    status="content_embedding_missing",
                    fail_open=True,
                )
                continue
            content_vector = valid_vector(
                raw_content_vector,
                expected_dimension=expected_dimension,
            )
            if content_vector is None:
                record(
                    index,
                    similarity=None,
                    would_filter=False,
                    status="content_embedding_invalid",
                    fail_open=True,
                )
                continue
            try:
                max_sim = max(
                    (
                        cosine_similarity(content_vector, interest_vector)
                        for interest_vector in interest_vectors
                    ),
                    default=0.0,
                )
            except Exception:
                logger.warning(
                    "embedding prefilter: similarity failed; candidate kept", exc_info=True
                )
                record(
                    index,
                    similarity=None,
                    would_filter=False,
                    status="similarity_error",
                    fail_open=True,
                )
                continue
            if not math.isfinite(max_sim):
                record(
                    index,
                    similarity=None,
                    would_filter=False,
                    status="similarity_error",
                    fail_open=True,
                )
                continue
            max_sim = max(0.0, min(1.0, max_sim))
            would_filter = max_sim < _EMBEDDING_PREFILTER_MIN_SIMILARITY
            if would_filter:
                filtered_scores[index] = round(max_sim * 0.5, 4)
            record(
                index,
                similarity=round(max_sim, 6),
                would_filter=would_filter,
                status=PREFILTER_OK_STATUS,
                fail_open=False,
            )
        return filtered_scores, decisions

    def _persist_prefilter_shadow_decisions(
        self,
        decisions: Sequence[PrefilterShadowDecision],
    ) -> bool:
        """Best-effort audit insert; failure never removes an LLM candidate."""

        if not decisions:
            return False
        database = getattr(self, "_database", None)
        recorder = getattr(database, "record_prefilter_shadow_decisions", None)
        if not callable(recorder):
            return False
        try:
            inserted = int(recorder([decision.as_storage_record() for decision in decisions]) or 0)
        except Exception:
            logger.warning(
                "prefilter shadow telemetry insert failed; candidates remain on LLM path",
                exc_info=True,
            )
            return False
        if inserted != len(decisions):
            logger.warning(
                "prefilter shadow telemetry insert incomplete: expected=%d inserted=%d",
                len(decisions),
                inserted,
            )
            return False
        return True

    def _complete_prefilter_shadow_decisions(
        self,
        decisions: Sequence[PrefilterShadowDecision],
        contents: Sequence[DiscoveredContent],
        raw_scores: Mapping[int, float],
        *,
        persisted: bool,
    ) -> None:
        """Best-effort score join; incomplete telemetry keeps the gate closed."""

        if not persisted or not decisions:
            return
        outcomes: list[PrefilterShadowOutcome] = []
        try:
            for decision in decisions:
                score = raw_scores.get(decision.content_index)
                if score is None:
                    # Provider/parse failures settle as synthetic 0.0 for the
                    # product path, but they are not eventual model scores.
                    # Leave those decisions unjoined so gate coverage closes.
                    continue
                threshold = self._admission_threshold_for_item(contents[decision.content_index])
                normalized_score = self._clamp_score(score)
                outcomes.append(
                    PrefilterShadowOutcome(
                        decision_id=decision.decision_id,
                        llm_score=normalized_score,
                        admission_threshold=threshold,
                        admission_result=normalized_score >= threshold,
                    )
                )
            if not outcomes:
                return
        except Exception:
            logger.warning(
                "prefilter shadow telemetry outcome construction failed; gate remains closed",
                exc_info=True,
            )
            return

        database = getattr(self, "_database", None)
        completer = getattr(database, "complete_prefilter_shadow_decisions", None)
        if not callable(completer):
            return
        try:
            updated = int(completer([outcome.as_storage_record() for outcome in outcomes]) or 0)
        except Exception:
            logger.warning(
                "prefilter shadow telemetry score join failed; gate remains closed",
                exc_info=True,
            )
            return
        if updated != len(outcomes):
            logger.warning(
                "prefilter shadow telemetry score join incomplete: expected=%d updated=%d",
                len(outcomes),
                updated,
            )

    def _supports_multimodal_evaluation(self) -> bool:
        override = getattr(self, "_multimodal_vision_supported_override", None)
        if override is not None:
            return bool(override)
        service = self._llm_service
        if service is None:
            return False
        for attr in ("supports_image_input", "supports_vision"):
            value = getattr(service, attr, None)
            if callable(value):
                with suppress(Exception):
                    return bool(value())
            if value is not None:
                return bool(value)
        return callable(getattr(service, "complete_multimodal_structured_task", None))

    def _effective_eval_batch_size(
        self,
        contents: list[DiscoveredContent],
        requested_batch_size: int,
    ) -> int:
        batch_size = max(1, int(requested_batch_size))
        self.multimodal_unavailable_reason = ""
        if not bool(getattr(self, "multimodal_evaluation_enabled", False)):
            return batch_size
        if not any((content.cover_url or "").strip() for content in contents):
            return batch_size
        if not self._supports_multimodal_evaluation():
            self.multimodal_unavailable_reason = (
                "Current evaluation model is not vision-capable; using text-only evaluation."
            )
            return batch_size
        return min(batch_size, int(getattr(self, "multimodal_batch_size", 8)))

    def _effective_eval_batch_concurrency(self) -> int:
        try:
            configured = int(
                getattr(
                    self,
                    "eval_batch_concurrency",
                    _DEFAULT_EVAL_BATCH_CONCURRENCY,
                )
            )
        except (TypeError, ValueError):
            configured = _DEFAULT_EVAL_BATCH_CONCURRENCY
        return max(1, min(16, configured))

    def register_strategy(self, strategy: DiscoveryStrategy) -> None:
        """Register a discovery strategy."""
        self._strategies = [item for item in self._strategies if item.name != strategy.name]
        self._strategies.append(strategy)
        logger.info("Registered discovery strategy: %s", strategy.name)

    def register_adapter(self, adapter: Any) -> None:
        """Register a :class:`SourceAdapter` for multi-source discovery.

        The adapter is stored in ``_adapter_registry`` keyed by its
        ``source_type``.  Phase 2+ will use this during recipe-driven
        discovery cycles.
        """
        if not hasattr(self, "_adapter_registry"):
            from openbiliclaw.sources.registry import AdapterRegistry

            self._adapter_registry = AdapterRegistry()
        self._adapter_registry.register(adapter)

    @property
    def adapter_registry(self) -> Any:
        """Return the adapter registry, creating it lazily if needed."""
        if not hasattr(self, "_adapter_registry"):
            from openbiliclaw.sources.registry import AdapterRegistry

            self._adapter_registry = AdapterRegistry()
        return self._adapter_registry

    async def discover(
        self,
        profile: SoulProfile,
        strategies: list[str] | None = None,
        limit: int = 30,
        *,
        fully_parallel: bool = False,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
        keywords: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
    ) -> list[DiscoveredContent]:
        """Run discovery with selected (or all) strategies.

        Args:
            profile: User soul profile for relevance evaluation.
            strategies: Optional list of strategy names to run.
                       If None, runs all registered strategies.
            fully_parallel: When True, skip the default two-phase split
                (search-first then others) and run every strategy in a
                single ``asyncio.gather``. Rate limiting still holds —
                ``bilibili_request_concurrency`` caps simultaneous HTTP
                requests and ``search_budget_total`` caps total search
                calls — so this only sacrifices the 2s cool-down between
                phases. Use for latency-critical flows (init bootstrap).
            strategy_limits: Optional per-strategy run limits. The final
                ``limit`` still caps returned/cached results; this only
                prevents a grouped refresh from giving every strategy the
                full platform deficit.
            pool_snapshot: Optional current pool distribution summary for
                strategies that can use pool-aware discovery guidance.
            keywords: Optional caller-supplied search keywords forwarded to
                search sub-strategies that accept a ``keywords`` kwarg (the
                unified keyword planner injection point). Non-search strategies
                never declare the kwarg, so they are unaffected. When ``None``,
                strategies generate their own keywords as before.
            keyword_ids: Optional ``keyword text → discovery_keywords.id`` map
                (P1.8 yield provenance) forwarded alongside ``keywords`` to
                search sub-strategies that declare a ``keyword_ids`` kwarg, so
                each produced item is stamped with the id of the word that
                produced it. ``None`` keeps the path attribution-free.

        Returns:
            Combined, deduplicated, and scored list of discovered content.
        """
        active = self._strategies
        if strategies:
            active = [s for s in self._strategies if s.name in strategies]

        if not active:
            return []

        effective_limit = max(1, min(limit, self._backfill_target_count))
        primary_results = await self._run_strategies(
            active,
            profile=profile,
            limit=effective_limit,
            fully_parallel=fully_parallel,
            strategy_limits=strategy_limits,
            pool_snapshot=pool_snapshot,
            keywords=keywords,
            keyword_ids=keyword_ids,
        )
        # Normalize topic_group using embeddings before dedup
        merged_primary = self._merge_and_rank(primary_results)
        await self._normalize_topic_groups(merged_primary)
        await self._normalize_topic_keys(merged_primary)
        merged_primary = self._apply_pool_snapshot_rerank(merged_primary, pool_snapshot)
        final_results = self._compress_topic_repeats(
            merged_primary,
            limit=effective_limit,
        )

        primary_target = min(self._target_primary_count, effective_limit)
        if len(final_results) < primary_target:
            backfill_results = await self._run_backfill(
                active,
                profile=profile,
                limit=effective_limit,
                existing=final_results,
                pool_snapshot=pool_snapshot,
            )
            all_results = self._merge_and_rank([*final_results, *backfill_results])
            await self._normalize_topic_groups(all_results)
            await self._normalize_topic_keys(all_results)
            all_results = self._apply_pool_snapshot_rerank(all_results, pool_snapshot)
            final_results = self._compress_topic_repeats(
                all_results,
                limit=effective_limit,
            )

        self._cache_results(final_results)
        return final_results

    async def produce_candidates(
        self,
        profile: SoulProfile,
        strategies: list[str] | None = None,
        limit: int = 30,
        *,
        fully_parallel: bool = False,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
        keywords: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
    ) -> list[DiscoveredContent]:
        """Fetch raw candidates without LLM evaluation or content_cache writes."""

        active = self._strategies
        if strategies:
            active = [s for s in self._strategies if s.name in strategies]
        if not active:
            return []

        effective_limit = max(1, min(limit, self._backfill_target_count))
        token = _RAW_CANDIDATE_MODE.set(True)
        try:
            raw_results = await self._run_strategies(
                active,
                profile=profile,
                limit=effective_limit,
                fully_parallel=fully_parallel,
                strategy_limits=strategy_limits,
                pool_snapshot=pool_snapshot,
                keywords=keywords,
                keyword_ids=keyword_ids,
            )
        finally:
            _RAW_CANDIDATE_MODE.reset(token)

        self._stamp_raw_candidate_thresholds(raw_results, active)
        return self._merge_duplicates(raw_results)[:effective_limit]

    @staticmethod
    def _stamp_raw_candidate_thresholds(
        results: list[DiscoveredContent],
        strategies: list[DiscoveryStrategy],
    ) -> None:
        thresholds: dict[str, float] = {}
        for strategy in strategies:
            threshold = float(getattr(strategy, "score_threshold", 0.0) or 0.0)
            if threshold > 0:
                thresholds[str(strategy.name).strip().lower()] = threshold
        if not thresholds:
            return
        for item in results:
            if float(item.score_threshold or 0.0) > 0:
                continue
            strategy_key = str(item.source_strategy or "").strip().lower()
            item_threshold = thresholds.get(strategy_key)
            if item_threshold is not None:
                item.score_threshold = item_threshold

    async def _normalize_topic_groups(
        self,
        results: list[DiscoveredContent],
    ) -> None:
        """Assign topic_group to items that lack one via embedding similarity.

        Items that already have a topic_group are trusted as-is — they were
        set by LLM evaluation or strategy-level inference and are already
        coarse labels.  Re-merging short Chinese labels via embedding produces
        false positives (e.g. "国际史实" → "人工智能" at threshold 0.82)
        because short text embeddings are deceptively close in cosine space.

        This method only operates on items WITHOUT a topic_group, attempting
        to assign them to an existing cluster from items that do have one.
        """
        if self._embedding_service is None or not results:
            return

        from openbiliclaw.llm.embedding import cosine_similarity

        # Build cluster centroids from items that already have a topic_group
        clusters: dict[str, list[float]] = {}
        for item in results:
            group = (item.topic_group or "").strip().lower()
            if not group or group in clusters:
                continue
            vec = await self._embedding_service.embed(group)
            if vec:
                clusters[group] = vec

        if not clusters:
            return

        # Only try to assign topic_group to items that don't have one
        # Use a stricter threshold for short-label merging
        threshold = min(0.92, self._embedding_service.similarity_threshold + 0.10)
        for item in results:
            if (item.topic_group or "").strip():
                continue
            topic = (item.topic_key or "").strip().lower()
            if not topic:
                continue
            vec = await self._embedding_service.embed(topic)
            if not vec:
                continue

            best_label: str | None = None
            best_sim = 0.0
            for label, centroid in clusters.items():
                sim = cosine_similarity(vec, centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_label = label

            if best_label is not None and best_sim >= threshold:
                item.topic_group = best_label
                logger.debug(
                    "Topic assigned: %r → %r (sim=%.3f)",
                    topic,
                    best_label,
                    best_sim,
                )

    async def _normalize_topic_keys(
        self,
        results: list[DiscoveredContent],
    ) -> None:
        """Normalize topic_keys across strategies via embedding-based clustering.

        Different strategies produce topic_keys at different granularities:
        - search: fine-grained LLM phrases ("moba经济曲线动态博弈")
        - trending/related_chain: B站 tname categories ("网络游戏")
        - explore: domain labels ("精密机械钟表修复与微观结构")

        This method clusters semantically similar keys and reassigns them
        to a canonical representative, so downstream diversity logic in
        _compress_topic_repeats correctly recognizes same-topic items.
        """
        if self._embedding_service is None or not results:
            return

        from openbiliclaw.llm.embedding import cosine_similarity

        # Step 1: Collect unique topic_keys and embed them
        unique_keys: list[str] = []
        seen: set[str] = set()
        for item in results:
            key = (item.topic_key or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique_keys.append(key)

        if len(unique_keys) <= 1:
            return

        # Embed all unique keys
        key_vectors: dict[str, list[float]] = {}
        for key in unique_keys:
            vec = await self._embedding_service.embed(key)
            if vec:
                key_vectors[key] = vec

        if len(key_vectors) <= 1:
            return

        # Step 2: Greedy agglomerative clustering
        threshold = self._embedding_service.similarity_threshold  # ~0.82
        clusters: list[tuple[str, list[str]]] = []

        for key, vec in key_vectors.items():
            best_cluster_idx: int | None = None
            best_sim = 0.0
            for idx, (canonical, _members) in enumerate(clusters):
                centroid = key_vectors.get(canonical)
                if centroid is None:
                    continue
                sim = cosine_similarity(vec, centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster_idx = idx

            if best_cluster_idx is not None and best_sim >= threshold:
                clusters[best_cluster_idx][1].append(key)
            else:
                clusters.append((key, [key]))

        # Step 3: For each cluster, pick canonical label (medium-length preferred)
        canonical_map: dict[str, str] = {}  # original_key → canonical_key
        for _canonical, members in clusters:
            if len(members) <= 1:
                continue
            best_label = members[0]
            best_score = self._label_quality_score(members[0])
            for member in members[1:]:
                score = self._label_quality_score(member)
                if score > best_score:
                    best_score = score
                    best_label = member
            for member in members:
                if member != best_label:
                    canonical_map[member] = best_label

        if not canonical_map:
            return

        # Step 4: Reassign topic_key on items
        for item in results:
            key = (item.topic_key or "").strip().lower()
            canonical_key = canonical_map.get(key)
            if canonical_key:
                logger.debug(
                    "Topic key normalized: %r → %r (strategy=%s)",
                    item.topic_key,
                    canonical_key,
                    item.source_strategy,
                )
                item.topic_key = canonical_key

    @staticmethod
    def _label_quality_score(label: str) -> float:
        """Score a topic label for use as canonical representative.

        Prefers medium-length labels (4-8 chars) that are descriptive
        but not overly specific.
        """
        length = len(label)
        if length <= 2:
            return 0.2
        if length <= 4:
            return 0.6
        if length <= 8:
            return 1.0
        if length <= 12:
            return 0.7
        return 0.4

    async def evaluate_content(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
        *,
        source_context: str = "",
    ) -> float:
        """Evaluate how relevant a piece of content is for the user.

        The core evaluation is based on the user's Soul — their deep personality
        and interests — not just surface-level metrics.

        Args:
            content: Content to evaluate.
            profile: User's soul profile.
            source_context: Discovery context hint for calibrating evaluation,
                e.g. "search_query: 纪录片 原理" or "explore_domain: 城市建筑叙事".

        Returns:
            Relevance score (0.0 - 1.0).
        """
        if self._llm_service is None:
            return 0.0

        from openbiliclaw.llm.prompts import content_evaluation_clock

        evaluated_at, evaluation_bucket = content_evaluation_clock()
        # Cache the complete evaluator input, not merely object identity. Profile
        # reloads with equivalent values should hit; content/context/model changes
        # must miss without issuing an embedding or LLM request. Publication
        # metadata and the hourly evaluation clock are prompt-visible too.
        profile_digest = self._evaluation_profile_digest(profile)
        cache_key = self._single_eval_cache_key(
            content,
            profile_digest=profile_digest,
            source_context=source_context,
            evaluation_bucket=evaluation_bucket,
        )
        cached = self._get_eval_cache_entry(cache_key)
        if cached is not None:
            score, reason, topic_group, style_key, franchise_key, temporal = (
                _decode_eval_cache_entry(cached)
            )
            normalized_reason = normalize_evaluation_reason(score, reason)
            if normalized_reason is not None:
                content.relevance_score = score
                content.relevance_reason = normalized_reason
                content.topic_group = topic_group
                content.style_key = normalize_style_key(style_key)
                content.franchise_key = franchise_key
                _apply_temporal_evaluation(
                    content,
                    temporal,
                    evaluated_at=evaluated_at,
                    evidence_text=_temporal_evidence_text(content),
                )
                return score

        prefilter_mode = self._normalize_eval_prefilter_mode(
            getattr(self, "eval_prefilter_mode", _EMBEDDING_PREFILTER_DEFAULT_MODE)
        )
        shadow_decisions: list[PrefilterShadowDecision] = []
        shadow_persisted = False
        if prefilter_mode != "off":
            prefiltered, shadow_decisions = await self._embedding_prefilter_shadow_analysis(
                [content],
                profile,
                source_context=source_context,
                profile_digest=profile_digest,
            )
            shadow_persisted = self._persist_prefilter_shadow_decisions(shadow_decisions)
            evidence_complete = len(shadow_decisions) == 1 and shadow_persisted
            if prefilter_mode == "enforce" and not evidence_complete:
                logger.warning(
                    "prefilter enforce decision telemetry unavailable; failing candidate open"
                )
                prefiltered = {}
            if 0 in prefiltered:
                prefilter_score = prefiltered[0]
                max_sim = prefilter_score * 2.0
                if prefilter_mode == "shadow":
                    logger.info(
                        "prefilter-shadow title=%r max_sim=%.4f strategy=%s",
                        content.title,
                        max_sim,
                        content.source_strategy,
                    )
                elif prefilter_mode == "enforce":
                    content.relevance_score = prefilter_score
                    content.relevance_reason = (
                        normalize_evaluation_reason(
                            prefilter_score,
                            _EMBEDDING_PREFILTER_REASON,
                        )
                        or ""
                    )
                    content.topic_group = ""
                    content.style_key = ""
                    content.franchise_key = ""
                    _apply_temporal_evaluation(
                        content,
                        TemporalEvaluation(),
                        evaluated_at=evaluated_at,
                        evidence_text=_temporal_evidence_text(content),
                    )
                    self._set_eval_cache_entry(
                        cache_key,
                        _eval_cache_entry_for_content(content),
                    )
                    return content.relevance_score

        from openbiliclaw.llm.prompts import build_content_evaluation_prompt

        content_summary = _single_evaluation_content_summary(content)
        recall = await self._related_interests_for_content_result(content, profile)
        if recall.related:
            content_summary["related_interests"] = recall.related

        messages = build_content_evaluation_prompt(
            profile_summary=self._evaluation_profile_summary(profile),
            content_summary=content_summary,
            source_context=source_context or content.source_strategy,
            source_platform=content.source_platform or "bilibili",
            evaluated_at=evaluated_at,
        )
        try:
            complete_structured = self._llm_service.complete_structured_task
            llm_call = complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                caller="discovery.evaluate_single",
                **without_core_memory_kwargs(complete_structured),
            )
            if self._concurrency is not None:
                response = await self._concurrency.run_llm(llm_call)
            else:
                response = await llm_call
            payload = parse_llm_json_tolerant(str(getattr(response, "content", "")).strip())
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object from content evaluation")
            validated_score = self._validated_model_score(payload.get("score"))
            if validated_score is None:
                raise ValueError("Expected finite content evaluation score in [0, 1]")
            score = validated_score
            checked_reason = validated_text_field(
                payload.get("reason", ""), field="reason", content_key=content.bvid
            )
            checked_topic_group = validated_text_field(
                payload.get("topic_group", ""), field="topic_group", content_key=content.bvid
            )
            checked_franchise = validated_text_field(
                payload.get("franchise_key", ""), field="franchise_key", content_key=content.bvid
            )
            if checked_reason is None or checked_topic_group is None or checked_franchise is None:
                # A non-string reason must not be repr'd into relevance_reason
                # and surface as delight copy.
                raise ValueError("Non-string field in content evaluation response")
            normalized_reason = normalize_evaluation_reason(score, checked_reason)
            if normalized_reason is None:
                raise ValueError("Non-string reason in content evaluation response")
            reason = normalized_reason
            topic_group = checked_topic_group
            franchise_key = checked_franchise
            style_key = normalize_style_key(payload.get("style_key", ""))
            temporal = parse_temporal_evaluation(payload)
        except Exception:
            logger.exception("Failed to evaluate discovered content: %s", content.bvid)
            return 0.0

        content.relevance_score = score
        content.relevance_reason = reason
        content.topic_group = topic_group
        content.style_key = style_key
        content.franchise_key = franchise_key
        _apply_temporal_evaluation(
            content,
            temporal,
            evaluated_at=evaluated_at,
            evidence_text=_temporal_evidence_text_from_prompt_item(content_summary),
        )
        if recall.complete:
            self._set_eval_cache_entry(
                cache_key,
                _eval_cache_entry_for_content(content),
            )
        self._complete_prefilter_shadow_decisions(
            shadow_decisions,
            [content],
            {0: score},
            persisted=shadow_persisted,
        )
        return score

    # Safety cap applied at the evaluator level regardless of caller.
    # Strategies that over-fetch (related_chain depth-2 fanout, explore
    # with expanded budget, etc.) would otherwise dump 400+ items into a
    # single discover run. 30 keeps each strategy's evaluation bounded
    # to a single LLM call when ``batch_size`` matches the cap (v0.3.25+
    # default — see below). Truncation is top-of-list (natural ranking
    # from strategies), and a WARNING is emitted so we see when
    # strategies hit the cap.
    #
    # v0.3.52+: cap raised 30 → 90 to evaluate ~3× more candidates per
    # discovery round. Production logs (2026-05-05) routinely truncated
    # 300-480 candidates down to 30 — 90% data wasted. The 30/batch
    # constant stays so each individual LLM call is the same size,
    # but ``_run_batch`` already gathers multiple batches in parallel
    # via ``asyncio.gather``, so the new cap means 3 parallel LLM
    # batches of 30 items each. Concurrency is bounded by
    # ``llm_evaluation_concurrency`` so we don't blow up provider
    # rate limits. Combined with v0.3.51's reasoning-disabled batches
    # (~30s each), three parallel batches finish in roughly the same
    # wall time as one used to take.
    _EVALUATE_BATCH_HARD_CAP = _EVALUATE_BATCH_HARD_CAP_DEFAULT

    async def evaluate_content_batch(
        self,
        contents: list[DiscoveredContent],
        profile: SoulProfile,
        *,
        source_context: str = "",
        batch_size: int = _DEFAULT_EVAL_BATCH_SIZE,
    ) -> list[float]:
        """Evaluate multiple content items with batched LLM calls.

        Groups items into batches of ``batch_size`` and sends one LLM
        call per batch instead of one per item.  Falls back to single
        evaluation for items that fail in a batch.

        The default text batch size is 45, with a hard cap of 90 and two
        worker slots by default. This keeps multimodal evaluation on its
        smaller image-aware batch size while letting long-context text
        models amortize the fixed profile/system prompt across more items.

        Returns scores in the same order as ``contents``.
        """
        if self._llm_service is None or not contents:
            return [0.0] * len(contents)

        from openbiliclaw.llm.prompts import content_evaluation_clock

        evaluated_at, evaluation_bucket = content_evaluation_clock()

        original_len = len(contents)
        if original_len > self._EVALUATE_BATCH_HARD_CAP:
            logger.warning(
                "evaluate_content_batch: truncating %d -> %d items (source=%s)",
                original_len,
                self._EVALUATE_BATCH_HARD_CAP,
                source_context or "mixed",
            )
            contents = contents[: self._EVALUATE_BATCH_HARD_CAP]

        scores: list[float] = [0.0] * len(contents)
        viewed_content_keys = self._recent_viewed_content_keys()
        if viewed_content_keys:
            eval_pairs = [
                (index, content)
                for index, content in enumerate(contents)
                if self._candidate_view_keys(content).isdisjoint(viewed_content_keys)
            ]
            skipped_viewed = len(contents) - len(eval_pairs)
            if skipped_viewed > 0:
                logger.info(
                    "eval_batch skipped %d recently viewed candidate(s) before LLM (source=%s)",
                    skipped_viewed,
                    source_context or "mixed",
                )
        else:
            eval_pairs = list(enumerate(contents))

        if not eval_pairs:
            if len(scores) < original_len:
                scores = scores + [0.0] * (original_len - len(scores))
            return scores

        eval_indices = [index for index, _content in eval_pairs]
        eval_contents = [content for _index, content in eval_pairs]
        normal_cache_enabled = self._batch_normal_cache_eligible(
            eval_contents,
            source_context=source_context,
        )

        def finalize_scores(*, effective_batch_size: int, apply_cached_caps: bool) -> list[float]:
            if apply_cached_caps:
                group_size = max(1, int(effective_batch_size))
                for start in range(0, len(eval_contents), group_size):
                    group_contents = eval_contents[start : start + group_size]
                    group_scores: list[float | None] = [
                        scores[eval_indices[index]]
                        for index in range(start, start + len(group_contents))
                    ]
                    self._apply_intra_batch_caps(group_contents, group_scores)
                    for offset, group_score in enumerate(group_scores):
                        scores[eval_indices[start + offset]] = float(group_score or 0.0)
                logger.info(
                    "eval_batch final caller recap: source=%s size=%d kept=%d",
                    source_context or "mixed",
                    len(eval_contents),
                    sum(scores[index] > 0 for index in eval_indices),
                )
            if len(scores) < original_len:
                return scores + [0.0] * (original_len - len(scores))
            return scores

        # Split into cached vs uncached. Batch eval consumes recent negative
        # exemplars, so the in-memory score cache is versioned by the actual
        # prompt-visible negative examples digest. A new unrelated event may
        # move the event-log waterline, but it should not evict exact eval
        # results when the negative anchors fed to the model did not change.
        negative_examples = self._get_negative_exemplars()
        if not negative_examples:
            negative_examples = None
        profile_digest = self._evaluation_profile_digest(profile)
        negative_digest = self._negative_examples_digest(negative_examples)
        uncached_indices: list[int] = []
        cache_hit_count = 0
        for i, content in enumerate(eval_contents):
            cache_key = self._batch_eval_cache_key(
                content,
                profile_digest=profile_digest,
                negative_digest=negative_digest,
                evaluation_bucket=evaluation_bucket,
                source_context=source_context,
            )
            cached = self._get_eval_cache_entry(cache_key) if normal_cache_enabled else None
            if cached is not None:
                # The cache tuple grew first to carry franchise_key and now
                # temporal semantics. Keep both legacy 4/5-field shapes safe
                # for in-flight processes during a rolling upgrade.
                score, reason, topic_group, style_key, franchise_key, temporal = (
                    _decode_eval_cache_entry(cached)
                )
                normalized_reason = normalize_evaluation_reason(score, reason)
                if normalized_reason is None:
                    uncached_indices.append(i)
                    continue
                style_key = normalize_style_key(style_key)
                content.relevance_score = score
                content.relevance_reason = normalized_reason
                content.topic_group = topic_group
                content.style_key = style_key
                content.franchise_key = franchise_key
                _apply_temporal_evaluation(
                    content,
                    temporal,
                    evaluated_at=evaluated_at,
                    evidence_text=_temporal_evidence_text(content),
                )
                scores[eval_indices[i]] = score
                cache_hit_count += 1
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            effective_batch_size = self._effective_eval_batch_size(eval_contents, batch_size)
            return finalize_scores(
                effective_batch_size=effective_batch_size,
                apply_cached_caps=cache_hit_count > 0,
            )

        prefilter_mode = self._normalize_eval_prefilter_mode(
            getattr(self, "eval_prefilter_mode", _EMBEDDING_PREFILTER_DEFAULT_MODE)
        )
        filtered_local_indices: set[int] = set()
        shadow_decisions: list[PrefilterShadowDecision] = []
        shadow_contents: list[DiscoveredContent] = []
        shadow_eval_indices: tuple[int, ...] = ()
        shadow_persisted = False
        if prefilter_mode != "off":
            prefilter_contents = [eval_contents[i] for i in uncached_indices]
            (
                prefiltered_scores,
                shadow_decisions,
            ) = await self._embedding_prefilter_shadow_analysis(
                prefilter_contents,
                profile,
                source_context=source_context,
                profile_digest=profile_digest,
            )
            shadow_contents = prefilter_contents
            shadow_eval_indices = tuple(uncached_indices)
            shadow_persisted = self._persist_prefilter_shadow_decisions(shadow_decisions)
            evidence_complete = (
                len(shadow_decisions) == len(prefilter_contents) and shadow_persisted
            )
            would_filter_count = len(prefiltered_scores)
            if prefilter_mode == "shadow":
                for local_index, prefilter_score in prefiltered_scores.items():
                    content = prefilter_contents[local_index]
                    logger.info(
                        "prefilter-shadow title=%r max_sim=%.4f strategy=%s",
                        content.title,
                        prefilter_score * 2.0,
                        content.source_strategy,
                    )
                logger.info(
                    "eval_batch embedding prefilter: mode=shadow source=%s in=%d "
                    "would_filter=%d to_llm=%d",
                    source_context or "mixed",
                    len(prefilter_contents),
                    would_filter_count,
                    len(uncached_indices),
                )
            elif prefilter_mode == "enforce":
                if (
                    len(prefilter_contents) > 1
                    and would_filter_count / len(prefilter_contents) > 0.5
                ):
                    logger.warning(
                        "eval_batch embedding prefilter kill-rate guard: source=%s in=%d "
                        "would_filter=%d to_llm=%d",
                        source_context or "mixed",
                        len(prefilter_contents),
                        would_filter_count,
                        len(uncached_indices),
                    )
                    prefiltered_scores = {}
                    would_filter_count = 0
                elif not evidence_complete:
                    logger.warning(
                        "prefilter enforce decision telemetry unavailable; failing batch open"
                    )
                    prefiltered_scores = {}
                    would_filter_count = 0
                else:
                    filtered_local_indices = set(prefiltered_scores)
                    for local_index, prefilter_score in prefiltered_scores.items():
                        content = prefilter_contents[local_index]
                        eval_content_index = uncached_indices[local_index]
                        content.relevance_score = prefilter_score
                        content.relevance_reason = (
                            normalize_evaluation_reason(
                                prefilter_score,
                                _EMBEDDING_PREFILTER_REASON,
                            )
                            or ""
                        )
                        content.topic_group = ""
                        content.style_key = ""
                        content.franchise_key = ""
                        _apply_temporal_evaluation(
                            content,
                            TemporalEvaluation(),
                            evaluated_at=evaluated_at,
                            evidence_text=_temporal_evidence_text(content),
                        )
                        scores[eval_indices[eval_content_index]] = prefilter_score
                        if normal_cache_enabled:
                            cache_key = self._batch_eval_cache_key(
                                content,
                                profile_digest=profile_digest,
                                negative_digest=negative_digest,
                                evaluation_bucket=evaluation_bucket,
                                source_context=source_context,
                            )
                            self._set_eval_cache_entry(
                                cache_key,
                                _eval_cache_entry_for_content(content),
                            )
                    uncached_indices = [
                        eval_content_index
                        for local_index, eval_content_index in enumerate(uncached_indices)
                        if local_index not in filtered_local_indices
                    ]
                logger.info(
                    "eval_batch embedding prefilter: mode=enforce source=%s in=%d "
                    "prefiltered=%d to_llm=%d",
                    source_context or "mixed",
                    len(prefilter_contents),
                    would_filter_count,
                    len(uncached_indices),
                )
                if not uncached_indices:
                    effective_batch_size = self._effective_eval_batch_size(
                        eval_contents,
                        batch_size,
                    )
                    return finalize_scores(
                        effective_batch_size=effective_batch_size,
                        apply_cached_caps=cache_hit_count > 0,
                    )

        batch_size = self._effective_eval_batch_size(
            [eval_contents[i] for i in uncached_indices],
            batch_size,
        )
        if self.multimodal_unavailable_reason:
            logger.info("eval_batch multimodal fallback: %s", self.multimodal_unavailable_reason)

        # Cache hits and enforce-prefilter removals compress the provider
        # prompt relative to the caller's stable grouping. Defer sibling-
        # dependent caps until every raw score is back in caller order so a
        # cold run and a full-cache replay cannot disagree at a boundary.
        caller_recap_needed = cache_hit_count > 0 or (
            normal_cache_enabled and bool(filtered_local_indices)
        )

        total_batches = (len(uncached_indices) + batch_size - 1) // batch_size

        # Opt-in learned relevance scorer; default eval_scorer="llm" keeps
        # this inert, and any learned failure falls through to the LLM path.
        if self._eval_scorer == "learned" and self._learned_scorer is not None:
            learned_contents = [eval_contents[i] for i in uncached_indices]
            learned_result: LearnedBatchResult | None = None
            try:
                learned_result = await self._learned_scorer.score_batch(
                    [content.to_cache_kwargs() for content in learned_contents],
                    profile,
                    source_context=source_context,
                )
            except Exception:
                logger.exception(
                    "eval_batch learned scorer failed; falling back to LLM (source=%s)",
                    source_context or "mixed",
                )
            if (
                learned_result is not None
                and learned_result.available
                and len(learned_result.scores) == len(learned_contents)
            ):
                learned_model_scores: dict[int, float] = {}
                for local_index, raw_score in zip(
                    uncached_indices, learned_result.scores, strict=True
                ):
                    validated = self._validated_model_score(raw_score)
                    if validated is None:
                        logger.warning(
                            "eval_batch learned scorer invalid score %r; using 0.0",
                            raw_score,
                        )
                        score = 0.0
                    else:
                        score = validated
                    content = eval_contents[local_index]
                    content.relevance_score = score
                    content.relevance_reason = (
                        normalize_evaluation_reason(score, _LEARNED_SCORER_REASON) or ""
                    )
                    scores[eval_indices[local_index]] = score
                    learned_model_scores[local_index] = score
                if not caller_recap_needed:
                    group_size = max(1, int(batch_size))
                    for start in range(0, len(uncached_indices), group_size):
                        group_indices = uncached_indices[start : start + group_size]
                        group_contents = [eval_contents[i] for i in group_indices]
                        group_scores = [scores[eval_indices[i]] for i in group_indices]
                        self._apply_intra_batch_caps(group_contents, group_scores)
                        for offset, group_score in enumerate(group_scores):
                            scores[
                                eval_indices[group_indices[offset]]
                            ] = float(group_score or 0.0)
                shadow_raw_scores = {
                    decision.content_index: learned_model_scores[
                        shadow_eval_indices[decision.content_index]
                    ]
                    for decision in shadow_decisions
                    if decision.content_index < len(shadow_eval_indices)
                    and shadow_eval_indices[decision.content_index]
                    in learned_model_scores
                }
                self._complete_prefilter_shadow_decisions(
                    shadow_decisions,
                    shadow_contents,
                    shadow_raw_scores,
                    persisted=shadow_persisted,
                )
                logger.info(
                    "eval_batch learned scorer: source=%s items=%d",
                    source_context or "mixed",
                    len(learned_contents),
                )
                return finalize_scores(
                    effective_batch_size=batch_size,
                    apply_cached_caps=caller_recap_needed,
                )
        eval_batch_concurrency = self._effective_eval_batch_concurrency()
        logger.info(
            "eval_batch start: source=%s items=%d batches=%d concurrency=%d (cached=%d)",
            source_context or "mixed",
            len(uncached_indices),
            total_batches,
            eval_batch_concurrency,
            len(eval_contents) - len(uncached_indices),
        )

        # Run multiple LLM batches concurrently, but keep this task's
        # own fanout bounded. The shared ``run_llm`` wrapper remains the
        # global provider-facing cap across all discovery work; this local
        # worker cap prevents one large eval job from creating unbounded
        # child tasks or occupying every global LLM slot.
        raw_model_scores: dict[int, float] = {}

        async def _run_batch(
            batch_idx: int,
            batch_indices: list[int],
        ) -> tuple[list[int], list[float]]:
            batch_contents = [eval_contents[i] for i in batch_indices]
            valid_batch_score_indices: set[int] = set()
            t0 = time.monotonic()
            batch_scores = await self._evaluate_batch(
                batch_contents,
                profile,
                source_context=source_context,
                negative_examples=negative_examples,
                evaluated_at=evaluated_at,
                evaluation_bucket=evaluation_bucket,
                normal_cache_enabled=normal_cache_enabled,
                apply_batch_caps=False,
                valid_score_indices=valid_batch_score_indices,
            )
            for batch_local_index, (eval_content_index, raw_score) in enumerate(
                zip(batch_indices, batch_scores, strict=True)
            ):
                if batch_local_index in valid_batch_score_indices:
                    raw_model_scores[eval_content_index] = float(raw_score)
            if not caller_recap_needed:
                self._apply_intra_batch_caps(batch_contents, batch_scores)
            elapsed = time.monotonic() - t0
            kept = sum(1 for s in batch_scores if s > 0)
            if caller_recap_needed:
                logger.info(
                    "eval_batch %d/%d provider done: source=%s size=%d elapsed=%.1fs "
                    "raw_positive=%d final_recap=pending",
                    batch_idx,
                    total_batches,
                    source_context or "mixed",
                    len(batch_indices),
                    elapsed,
                    kept,
                )
                return batch_indices, batch_scores
            # v0.3.31+: diversity snapshot of the kept items so we can
            # see whether discovery is feeding the pool with variety or
            # 30 candidates that all collapse to the same topic_group.
            kept_items = [batch_contents[i] for i, s in enumerate(batch_scores) if s > 0]
            topics: Counter[str] = Counter(
                (getattr(c, "topic_group", "") or "untagged").strip().lower() for c in kept_items
            )
            styles: Counter[str] = Counter(
                (getattr(c, "style_key", "") or "untagged").strip().lower() for c in kept_items
            )
            franchises: Counter[str] = Counter(
                (getattr(c, "franchise_key", "") or "").strip().lower() for c in kept_items
            )
            del franchises[""]  # don't count non-franchise items
            top_topic = topics.most_common(1)[0] if topics else ("", 0)
            top_franchise = franchises.most_common(1)[0] if franchises else ("", 0)
            logger.info(
                "eval_batch %d/%d done: source=%s size=%d elapsed=%.1fs kept=%d "
                "diversity={topics: %d uniq, top=%s×%d (%.0f%%); styles: %d uniq, "
                "top=%s×%d; franchises: %d uniq%s}",
                batch_idx,
                total_batches,
                source_context or "mixed",
                len(batch_indices),
                elapsed,
                kept,
                len(topics),
                top_topic[0] or "—",
                top_topic[1],
                (top_topic[1] / kept * 100) if kept else 0,
                len(styles),
                styles.most_common(1)[0][0] if styles else "—",
                styles.most_common(1)[0][1] if styles else 0,
                len(franchises),
                f", top_franchise={top_franchise[0]}×{top_franchise[1]}"
                if top_franchise[1] > 1
                else "",
            )
            return batch_indices, batch_scores

        batch_jobs: list[tuple[int, list[int]]] = []
        for batch_idx, batch_start in enumerate(
            range(0, len(uncached_indices), batch_size), start=1
        ):
            batch_indices = uncached_indices[batch_start : batch_start + batch_size]
            batch_jobs.append((batch_idx, batch_indices))

        results: list[tuple[list[int], list[float]] | None] = [None] * len(batch_jobs)
        next_job_index = 0
        worker_count = min(eval_batch_concurrency, len(batch_jobs))

        async def _worker() -> None:
            nonlocal next_job_index
            while next_job_index < len(batch_jobs):
                job_index = next_job_index
                next_job_index += 1
                batch_idx, batch_indices = batch_jobs[job_index]
                results[job_index] = await _run_batch(batch_idx, batch_indices)

        await asyncio.gather(*(_worker() for _ in range(worker_count)))

        for result in results:
            if result is None:
                continue
            batch_indices, batch_scores = result
            for idx, batch_score in zip(batch_indices, batch_scores, strict=True):
                scores[eval_indices[idx]] = batch_score

        # Cache entries hold raw model scores. Reapply batch-dependent caps
        # against the stable caller grouping whenever a hit or a cached
        # enforce-prefilter result participated; ordinary cold batches were
        # capped in their actual provider chunks above.
        final_scores = finalize_scores(
            effective_batch_size=batch_size,
            apply_cached_caps=caller_recap_needed,
        )
        shadow_raw_scores = {
            decision.content_index: raw_model_scores[shadow_eval_indices[decision.content_index]]
            for decision in shadow_decisions
            if decision.content_index < len(shadow_eval_indices)
            and shadow_eval_indices[decision.content_index] in raw_model_scores
        }
        self._complete_prefilter_shadow_decisions(
            shadow_decisions,
            shadow_contents,
            shadow_raw_scores,
            persisted=shadow_persisted,
        )
        return final_scores

    def _recent_viewed_content_keys(self) -> set[str]:
        database = getattr(self, "_database", None)
        get_recent = getattr(database, "get_seen_content_keys", None)
        log_name = "get_seen_content_keys"
        if not callable(get_recent):
            get_recent = getattr(database, "get_recent_viewed_content_keys", None)
            log_name = "get_recent_viewed_content_keys"
        if not callable(get_recent):
            get_recent = getattr(database, "get_seen_bvids", None)
            log_name = "get_seen_bvids"
        if not callable(get_recent):
            get_recent = getattr(database, "get_recent_viewed_bvids", None)
            log_name = "get_recent_viewed_bvids"
        if not callable(get_recent):
            return set()
        try:
            raw = get_recent()
        except Exception:
            logger.debug("%s failed", log_name, exc_info=True)
            return set()
        return {str(item).strip() for item in raw if str(item).strip()}

    @staticmethod
    def _candidate_view_keys(content: DiscoveredContent) -> set[str]:
        platform = normalize_source_platform(
            content.source_platform,
            default="bilibili" if content.bvid else "",
        )

        keys: set[str] = set()
        for value in {content.bvid, content.content_id}:
            content_id = str(value or "").strip()
            if not content_id:
                continue
            keys.add(content_id)
            if platform:
                keys.add(f"{platform}:{content_id}")
        return keys

    def _negative_exemplar_revision(self) -> int | None:
        """Return the event-log revision used for negative-aware eval cache keys."""
        database = cast(
            "SupportsNegativeExemplarStore | None",
            getattr(self, "_database", None),
        )
        if database is None:
            return None
        try:
            latest_id = database.get_latest_event_id()
        except Exception:
            logger.debug("negative_exemplars: get_latest_event_id failed", exc_info=True)
            return None
        if latest_id is None:
            return None
        return int(latest_id)

    def _get_negative_exemplars(self) -> list[dict[str, object]] | None:
        """Return recent negative exemplars, refreshing the cache when stale.

        Cache key: (latest_event_id, time bucket). 5-minute TTL keeps the
        I/O flat across batches; latest-event-id invalidation picks up
        fresh negatives as soon as the user records one. Storage failures
        return None so the eval-batch always runs.
        """
        # Defensive getattr: some test fixtures construct the engine via
        # ``__new__`` and skip ``__init__`` entirely, so `_database` and
        # `_negative_exemplars_cache` may not exist as attributes.
        database = cast(
            "SupportsNegativeExemplarStore | None",
            getattr(self, "_database", None),
        )
        if database is None:
            return None

        from openbiliclaw.soul.negative_exemplars import recent_negative_exemplars

        latest_id = self._negative_exemplar_revision()

        cached = cast(
            "tuple[float, int | None, list[dict[str, object]]] | None",
            getattr(self, "_negative_exemplars_cache", None),
        )
        if cached is not None:
            cached_ts, cached_latest_id, cached_exemplars = cached
            if cached_latest_id == latest_id and (time.monotonic() - cached_ts) < 300:
                return cached_exemplars

        try:
            exemplars = recent_negative_exemplars(database)
        except Exception:
            logger.debug("negative_exemplars: refresh failed", exc_info=True)
            return None

        self._negative_exemplars_cache = (time.monotonic(), latest_id, exemplars)
        return exemplars

    def _evaluation_profile_digest(self, profile: SoulProfile) -> str:
        """Digest the structured profile slice and recall pool visible to evaluation."""

        compacted = self._evaluation_profile_summary(profile)
        return stable_json_digest(
            {
                "summary": compacted,
                "recall_pool": _evaluation_recall_pool_digest_payload(profile),
            }
        )

    @staticmethod
    def _evaluation_profile_summary(profile: SoulProfile) -> dict[str, object]:
        return compact_evaluation_profile_summary(build_profile_summary(profile))

    async def _related_interests_for_content(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
        *,
        top_k: int = 3,
    ) -> list[str]:
        """Return content-relevant tail-interest names for one candidate."""

        result = await self._related_interests_for_content_result(
            content,
            profile,
            top_k=top_k,
        )
        return result.related

    async def _related_interests_for_content_result(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
        *,
        top_k: int = 3,
    ) -> _RelatedInterestRecall:
        """Return recalled labels plus whether recall completed without degradation."""

        embedding_service = getattr(self, "_embedding_service", None)
        if embedding_service is None:
            return _RelatedInterestRecall([], True)
        interests = _evaluation_recall_interests(profile)
        if not interests:
            return _RelatedInterestRecall([], True)
        try:
            interest_vectors = await self._embed_related_interest_vectors(
                embedding_service,
                interests,
            )
            interest_dimension = self._complete_embedding_vector_dimension(
                interest_vectors,
                expected_count=len(interests),
            )
            if interest_dimension == 0:
                return _RelatedInterestRecall([], False)
            content_vec = await embedding_service.embed(
                self._embedding_prefilter_content_text(content)
            )
        except Exception:
            logger.debug("related_interests: embedding failed", exc_info=True)
            return _RelatedInterestRecall([], False)
        if self._embedding_vector_dimension(content_vec) != interest_dimension:
            return _RelatedInterestRecall([], False)
        try:
            related = self._score_related_interests(
                content_vec,
                interests,
                interest_vectors,
                top_k=top_k,
            )
        except Exception:
            logger.debug("related_interests: scoring failed", exc_info=True)
            return _RelatedInterestRecall([], False)
        return _RelatedInterestRecall(related, True)

    async def _related_interests_for_batch(
        self,
        contents: Sequence[DiscoveredContent],
        profile: SoulProfile,
        *,
        top_k: int = 3,
    ) -> dict[int, list[str]]:
        result = await self._related_interests_for_batch_result(
            contents,
            profile,
            top_k=top_k,
        )
        return result.related_by_index

    async def _related_interests_for_batch_result(
        self,
        contents: Sequence[DiscoveredContent],
        profile: SoulProfile,
        *,
        top_k: int = 3,
    ) -> _BatchRelatedInterestRecall:
        """Return per-item recalls and indexes safe to store in the normal cache."""

        all_indices = frozenset(range(len(contents)))
        embedding_service = getattr(self, "_embedding_service", None)
        if embedding_service is None or not contents:
            return _BatchRelatedInterestRecall({}, all_indices)
        interests = _evaluation_recall_interests(profile)
        if not interests:
            return _BatchRelatedInterestRecall({}, all_indices)
        try:
            interest_vectors = await self._embed_related_interest_vectors(
                embedding_service,
                interests,
            )
        except Exception:
            logger.debug("related_interests: interest embedding batch failed", exc_info=True)
            return _BatchRelatedInterestRecall({}, frozenset())
        interest_dimension = self._complete_embedding_vector_dimension(
            interest_vectors,
            expected_count=len(interests),
        )
        if interest_dimension == 0:
            return _BatchRelatedInterestRecall({}, frozenset())

        related_by_index: dict[int, list[str]] = {}
        complete_indices: set[int] = set()
        for index, content in enumerate(contents):
            try:
                content_vec = await embedding_service.embed(
                    self._embedding_prefilter_content_text(content)
                )
            except Exception:
                logger.debug(
                    "related_interests: content embedding failed for %s",
                    content.content_id or content.bvid or content.title,
                    exc_info=True,
                )
                continue
            if self._embedding_vector_dimension(content_vec) != interest_dimension:
                continue
            try:
                related = self._score_related_interests(
                    content_vec,
                    interests,
                    interest_vectors,
                    top_k=top_k,
                )
            except Exception:
                logger.debug(
                    "related_interests: scoring failed for %s",
                    content.content_id or content.bvid or content.title,
                    exc_info=True,
                )
                continue
            complete_indices.add(index)
            if related:
                related_by_index[index] = related
        return _BatchRelatedInterestRecall(
            related_by_index,
            frozenset(complete_indices),
        )

    @staticmethod
    def _embedding_vector_dimension(vector: object) -> int:
        if not isinstance(vector, list) or not vector:
            return 0
        if any(isinstance(value, bool) or not isinstance(value, int | float) for value in vector):
            return 0
        values = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in values):
            return 0
        return len(values)

    @classmethod
    def _complete_embedding_vector_dimension(
        cls,
        vectors: Sequence[object],
        *,
        expected_count: int,
    ) -> int:
        if len(vectors) != expected_count or expected_count <= 0:
            return 0
        dimensions = {cls._embedding_vector_dimension(vector) for vector in vectors}
        if len(dimensions) != 1:
            return 0
        dimension = next(iter(dimensions))
        return dimension if dimension > 0 else 0

    @staticmethod
    async def _embed_related_interest_vectors(
        embedding_service: SupportsEmbeddingService,
        interests: list[dict[str, object]],
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for interest in interests:
            vector = await embedding_service.embed(str(interest["name"]))
            vectors.append(vector)
        return vectors

    @staticmethod
    def _score_related_interests(
        content_vec: list[float],
        interests: list[dict[str, object]],
        interest_vectors: list[list[float]],
        *,
        top_k: int,
    ) -> list[str]:
        from openbiliclaw.llm.embedding import cosine_similarity

        scored: list[tuple[dict[str, object], float]] = []
        for interest, interest_vec in zip(interests, interest_vectors, strict=False):
            if not interest_vec:
                continue
            weight = _coerce_recall_weight(interest.get("weight", 0.0))
            sim = cosine_similarity(content_vec, interest_vec)
            if sim < _EVAL_RECALL_MIN_SIMILARITY:
                # Recall is a targeted hint, not a mandatory tag: an item
                # unrelated to every tail interest gets no entries at all.
                continue
            scored.append((interest, sim * 0.7 + weight * 0.3))

        scored.sort(key=lambda item: -item[1])
        limit = max(0, int(top_k))
        # Plain name strings: ~30 chars per entry in the indent=2 prompt JSON
        # versus ~300 for {name, category} objects — the field must stay far
        # cheaper than the profile tokens it recalls.
        return [str(interest["name"]) for interest, _score in scored[:limit]]

    def _evaluation_profile_prompt_cache_obj(self) -> PromptLayerRenderCache:
        """Return eval profile prompt cache, creating it for lightweight tests."""

        cache = getattr(self, "_evaluation_profile_prompt_cache", None)
        if not isinstance(cache, PromptLayerRenderCache):
            cache = PromptLayerRenderCache()
            self._evaluation_profile_prompt_cache = cache
        return cache

    def evaluation_profile_prompt_cache_stats(self) -> dict[str, dict[str, Any]]:
        """Return eval profile prompt-layer cache stats."""

        return self._evaluation_profile_prompt_cache_obj().stats()

    @staticmethod
    def _negative_examples_digest(examples: list[dict[str, object]] | None) -> str:
        return stable_json_digest(examples or [])

    def _evaluation_embedding_namespace(self) -> str:
        """Return a stable recall namespace without performing an embed call."""

        embedding_service = getattr(self, "_embedding_service", None)
        if embedding_service is None:
            return stable_json_digest(
                {
                    "namespace_contract": "evaluation-embedding-v1",
                    "service_type": "disabled",
                }
            )

        service_type = type(embedding_service)
        identity: dict[str, object] = {
            "service_type": f"{service_type.__module__}.{service_type.__qualname__}",
            "recall_pool_cap": _EVAL_RECALL_POOL_CAP,
            "recall_min_similarity": _EVAL_RECALL_MIN_SIMILARITY,
        }
        for attribute in (
            "embedding_fingerprint",
            "cache_model_namespace",
            "embedding_model",
            "embedding_provider",
            "similarity_threshold",
        ):
            try:
                value = getattr(embedding_service, attribute, None)
            except Exception:
                value = None
            if isinstance(value, str | bool | int | float):
                identity[attribute] = value
        return stable_json_digest(identity)

    @staticmethod
    def _batch_prompt_source_platform(contents: Sequence[DiscoveredContent]) -> str:
        platforms = {_batch_evaluation_platform(content) for content in contents}
        return "mixed" if len(platforms) > 1 else next(iter(platforms), "bilibili")

    def _batch_normal_cache_eligible(
        self,
        contents: Sequence[DiscoveredContent],
        *,
        source_context: str,
    ) -> bool:
        """Return whether per-item keys fully determine batch-level prompt metadata.

        Mixed-platform batches render an aggregate ``mixed`` platform, while a batch
        without an explicit context inherits that value from its first item. Either
        value can change after a partial cache hit, so heterogeneous calls bypass the
        normal cache. Sparse transports also lift homogeneous content types into
        batch defaults, so mixed-type treatment batches bypass member caching.
        Vision attempts bypass it until keys can cover the actual prepared image
        bytes rather than only their URLs.
        """

        if not contents:
            return False
        same_platform = len({_batch_evaluation_platform(content) for content in contents}) == 1
        candidate_transport = (
            str(
                getattr(
                    self,
                    "evaluation_candidate_transport",
                    _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT,
                )
            )
            .strip()
            .lower()
        )
        stable_content_type = (
            candidate_transport == "production"
            or len({_batch_evaluation_content_type(content) for content in contents}) == 1
        )
        stable_context = (
            bool(source_context) or len({content.source_strategy for content in contents}) == 1
        )
        multimodal_attempted = (
            bool(getattr(self, "multimodal_evaluation_enabled", False))
            and self._supports_multimodal_evaluation()
            and any((content.cover_url or "").strip() for content in contents)
        )
        return same_platform and stable_content_type and stable_context and not multimodal_attempted

    def _single_eval_cache_key(
        self,
        content: DiscoveredContent,
        *,
        profile_digest: str,
        source_context: str = "",
        evaluation_bucket: str = "",
    ) -> str:
        if not evaluation_bucket:
            from openbiliclaw.llm.prompts import content_evaluation_clock

            _evaluated_at, evaluation_bucket = content_evaluation_clock()
        effective_source_context = source_context or content.source_strategy
        prompt_digest = stable_json_digest(
            {
                "content_summary": _single_evaluation_content_summary(content),
                "source_context": effective_source_context,
                "source_platform": content.source_platform or "bilibili",
            }
        )
        prefilter_mode = self._normalize_eval_prefilter_mode(
            getattr(self, "eval_prefilter_mode", "")
        )
        return (
            f"{_EVAL_BATCH_CACHE_VERSION}:single:"
            f"{self._content_identity(content)}:content:{prompt_digest}:"
            f"evaluation_bucket:{evaluation_bucket}:"
            f"profile:{profile_digest}:embed:{self._evaluation_embedding_namespace()}:"
            f"prefilter:{prefilter_mode}"
        )

    def _batch_eval_cache_key(
        self,
        content: DiscoveredContent,
        *,
        profile_digest: str,
        negative_digest: str,
        evaluation_bucket: str = "",
        source_context: str = "",
    ) -> str:
        if not evaluation_bucket:
            from openbiliclaw.llm.prompts import content_evaluation_clock

            _evaluated_at, evaluation_bucket = content_evaluation_clock()
        effective_batch_context = source_context or content.source_strategy
        effective_batch_platform = _batch_evaluation_platform(content)
        candidate_transport = (
            str(
                getattr(
                    self,
                    "evaluation_candidate_transport",
                    _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT,
                )
            )
            .strip()
            .lower()
        )
        production_content_item = _batch_evaluation_content_item(
            content,
            source_context=source_context,
        )
        prompt_visible_content: object = production_content_item
        cache_content_identity = self._content_identity(content)
        if candidate_transport != "production":
            prompt_visible_content = build_canonical_evaluation_batch(
                [production_content_item]
            ).as_payload()
            cache_content_identity = f"canonical:{stable_json_digest(prompt_visible_content)}"
        prompt_digest = stable_json_digest(
            {
                "content_item": prompt_visible_content,
                "batch_source_context": effective_batch_context,
                "batch_source_platform": effective_batch_platform,
                "multimodal_enabled": bool(getattr(self, "multimodal_evaluation_enabled", False)),
            }
        )
        prefilter_mode = self._normalize_eval_prefilter_mode(
            getattr(self, "eval_prefilter_mode", "")
        )
        transport_suffix = (
            "" if candidate_transport == "production" else f":transport:{candidate_transport}"
        )
        return (
            f"{_EVAL_BATCH_CACHE_VERSION}:batch:"
            f"{cache_content_identity}:content:{prompt_digest}:"
            f"evaluation_bucket:{evaluation_bucket}:"
            f"profile:{profile_digest}:neg:{negative_digest}:"
            f"embed:{self._evaluation_embedding_namespace()}:"
            f"prefilter:{prefilter_mode}{transport_suffix}"
        )

    async def _evaluate_batch_once(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
        *,
        source_context: str = "",
        negative_examples: object = _NEGATIVE_EXAMPLES_UNSET,
        evaluated_at: str = "",
        evaluation_bucket: str = "",
        normal_cache_enabled: bool = True,
    ) -> list[float | None]:
        """Send one LLM call for a batch of items."""
        from openbiliclaw.llm.prompts import (
            build_batch_content_evaluation_prompt,
            content_evaluation_clock,
        )

        if not evaluated_at or not evaluation_bucket:
            current_evaluated_at, current_bucket = content_evaluation_clock()
            evaluated_at = evaluated_at or current_evaluated_at
            evaluation_bucket = evaluation_bucket or current_bucket

        profile_data = self._evaluation_profile_summary(profile)
        effective_batch_context = source_context or (batch[0].source_strategy if batch else "")
        effective_batch_platform = self._batch_prompt_source_platform(batch)
        candidate_transport = (
            str(
                getattr(
                    self,
                    "evaluation_candidate_transport",
                    _DEFAULT_EVALUATION_CANDIDATE_TRANSPORT,
                )
            )
            .strip()
            .lower()
        )
        if candidate_transport not in _EVALUATION_CANDIDATE_TRANSPORTS:
            raise ValueError(f"unsupported evaluation candidate transport: {candidate_transport!r}")
        normal_cache_enabled = normal_cache_enabled and self._batch_normal_cache_eligible(
            batch,
            source_context=source_context,
        )
        recall = await self._related_interests_for_batch_result(batch, profile)
        content_items: list[dict[str, object]] = []
        for index, c in enumerate(batch):
            item = _batch_evaluation_content_item(c, source_context=source_context)
            related_interests = recall.related_by_index.get(index)
            if related_interests:
                item["related_interests"] = related_interests
            content_items.append(item)
        image_inputs: list[dict[str, str]] = []
        multimodal_enabled = bool(getattr(self, "multimodal_evaluation_enabled", False))
        if (
            multimodal_enabled
            and self._supports_multimodal_evaluation()
            and any((content.cover_url or "").strip() for content in batch)
        ):
            from openbiliclaw.discovery import multimodal

            prepared_images = await multimodal.prepare_cover_image_inputs(
                batch,
                max_px=int(getattr(self, "multimodal_image_max_px", 384)),
                quality=int(getattr(self, "multimodal_image_quality", 72)),
                timeout_seconds=int(getattr(self, "multimodal_image_timeout_seconds", 6)),
            )
            image_ids = {image.content_id for image in prepared_images}
            if image_ids:
                if candidate_transport == "production":
                    for item in content_items:
                        content_id = str(item.get("content_id") or item.get("bvid") or "")
                        if content_id in image_ids:
                            item["cover_image_ref"] = f"cover:{content_id}"
                    image_inputs = [image.to_llm_input() for image in prepared_images]
                else:
                    # Treatment anchors must not leak global identifiers. Only
                    # unambiguous one-to-one image/content identities are kept;
                    # ambiguous inputs are omitted together with their text
                    # anchor so they cannot bind to the wrong request member.
                    content_identity_counts = Counter(
                        str(item.get("content_id") or item.get("bvid") or "")
                        for item in content_items
                    )
                    prepared_identity_counts = Counter(
                        image.content_id for image in prepared_images
                    )
                    local_id_by_content_identity = {
                        content_identity: str(index)
                        for index, item in enumerate(content_items)
                        if (
                            (
                                content_identity := str(
                                    item.get("content_id") or item.get("bvid") or ""
                                )
                            )
                            and content_identity_counts[content_identity] == 1
                            and prepared_identity_counts[content_identity] == 1
                        )
                    }
                    for item in content_items:
                        content_identity = str(item.get("content_id") or item.get("bvid") or "")
                        local_id = local_id_by_content_identity.get(content_identity)
                        if local_id is not None:
                            item["cover_image_ref"] = f"cover:{local_id}"
                    for image in prepared_images:
                        local_id = local_id_by_content_identity.get(image.content_id)
                        if local_id is None:
                            continue
                        image_input = image.to_llm_input()
                        image_input["content_id"] = local_id
                        image_inputs.append(image_input)

        canonical_batch: CanonicalEvaluationBatch | None = None
        local_content_by_id: dict[str, DiscoveredContent] = {}
        candidate_block: str | None = None
        if candidate_transport != "production":
            canonical_batch = build_canonical_evaluation_batch(content_items)
            local_content_by_id = dict(zip(canonical_batch.local_ids, batch, strict=True))
            if candidate_transport == "sparse-json":
                candidate_block = render_sparse_evaluation_json(canonical_batch)
            else:
                candidate_block = encode_evaluation_row_wire(canonical_batch.as_payload())
        if negative_examples is _NEGATIVE_EXAMPLES_UNSET:
            negative_examples = self._get_negative_exemplars()
        # Treat empty list as "no examples" so the user-message stays
        # byte-identical to the no-examples shape on cold-start users.
        if not negative_examples:
            negative_examples = None
        negative_examples_for_prompt = cast("list[dict[str, object]] | None", negative_examples)
        profile_digest = self._evaluation_profile_digest(profile)
        negative_digest = self._negative_examples_digest(negative_examples_for_prompt)
        compact_json = bool(getattr(self, "compact_evaluation_json", False))
        profile_blocks = self._evaluation_profile_prompt_cache_obj().render_json_layers(
            evaluation_profile_prompt_layers(profile_data),
            compact=compact_json,
        )
        messages = build_batch_content_evaluation_prompt(
            profile_summary=profile_data,
            profile_blocks=profile_blocks,
            content_items=content_items,
            source_context=effective_batch_context,
            source_platform=effective_batch_platform,
            negative_examples=negative_examples_for_prompt,
            evaluated_at=evaluated_at,
            compact_json=compact_json,
            candidate_block=candidate_block,
            local_result_ids=canonical_batch is not None,
        )

        assert self._llm_service is not None
        try:
            multimodal_call = getattr(
                self._llm_service,
                "complete_multimodal_structured_task",
                None,
            )
            if image_inputs and callable(multimodal_call):
                kwargs: dict[str, Any] = {
                    "system_instruction": messages[0]["content"],
                    "user_input": messages[1]["content"],
                    "image_inputs": image_inputs,
                    "max_tokens": 4096,
                    "reasoning_effort": "",
                    "caller": "discovery.evaluate_batch",
                }
                kwargs.update(without_core_memory_kwargs(multimodal_call))
                llm_call = multimodal_call(**kwargs)
            else:
                kwargs = {
                    "system_instruction": messages[0]["content"],
                    "user_input": messages[1]["content"],
                    # v0.3.51+: explicitly disable provider thinking. This
                    # task is structured scoring (return JSON array), not
                    # reasoning — production logs showed 8-16 min/batch
                    # with reasoning enabled, dropping to ~30s without.
                    # 4096 max_tokens covers the observed 1500-3000 token
                    # output of a 30-item JSON array without making providers
                    # reserve an unnecessarily large per-request quota.
                    "max_tokens": 4096,
                    "caller": "discovery.evaluate_batch",
                }
                from openbiliclaw.llm.task_options import call_accepts_keyword

                complete_structured = self._llm_service.complete_structured_task
                if call_accepts_keyword(complete_structured, "reasoning_effort"):
                    kwargs["reasoning_effort"] = ""
                kwargs.update(without_core_memory_kwargs(complete_structured))
                llm_call = complete_structured(**kwargs)
            if self._concurrency is not None:
                response = await self._concurrency.run_llm(llm_call)
            else:
                response = await llm_call
        except Exception:
            raise

        raw = str(getattr(response, "content", "")).strip()
        payload = _parse_batch_evaluation_payload(raw)
        if payload is None:
            return [None] * len(batch)

        local_payload: list[dict[str, Any] | None] | None = None
        payload_by_id: dict[str, dict[str, Any]] | None = None
        if canonical_batch is not None:
            local_payload = resolve_local_evaluation_results(
                payload,
                canonical_batch.local_ids,
            )
        else:
            payload_by_id = _batch_results_by_content_key(payload, batch)
            if payload_by_id is None and len(payload) != len(batch):
                logger.warning(
                    "Batch evaluation result count mismatch without IDs (%d results for %d items), "
                    "falling back to single eval",
                    len(payload),
                    len(batch),
                )
                return [None] * len(batch)

        results: list[float | None] = []
        for i, content in enumerate(batch):
            if canonical_batch is not None:
                local_id = canonical_batch.local_ids[i]
                if local_content_by_id.get(local_id) is not content:
                    raise RuntimeError("request-local evaluation identity map is inconsistent")
                assert local_payload is not None
                raw_item = local_payload[i]
            elif payload_by_id is None:
                raw_item = payload[i] if i < len(payload) else None
            else:
                raw_item = next(
                    (
                        payload_by_id[key]
                        for key in _content_result_keys(content)
                        if key in payload_by_id
                    ),
                    None,
                )
            if raw_item is None:
                results.append(None)
                continue
            if not isinstance(raw_item, dict):
                results.append(None)
                continue
            item_result: dict[str, Any] = raw_item
            score = self._validated_model_score(item_result.get("score"))
            if score is None:
                results.append(None)
                continue
            checked_reason = validated_text_field(
                item_result.get("reason", ""), field="reason", content_key=content.bvid
            )
            topic_group = validated_text_field(
                item_result.get("topic_group", ""), field="topic_group", content_key=content.bvid
            )
            franchise_key = validated_text_field(
                item_result.get("franchise_key", ""),
                field="franchise_key",
                content_key=content.bvid,
            )
            if checked_reason is None or topic_group is None or franchise_key is None:
                # Treat a non-string field as a missing result so the item is
                # retried instead of persisting a repr as relevance_reason.
                results.append(None)
                continue
            reason = normalize_evaluation_reason(score, checked_reason)
            if reason is None:
                results.append(None)
                continue
            style_key = normalize_style_key(item_result.get("style_key", ""))
            temporal = parse_temporal_evaluation(item_result)

            content.relevance_score = score
            content.relevance_reason = reason
            content.topic_group = topic_group
            content.style_key = style_key
            content.franchise_key = franchise_key
            _apply_temporal_evaluation(
                content,
                temporal,
                evaluated_at=evaluated_at,
                evidence_text=_temporal_evidence_text_from_prompt_item(
                    canonical_batch.items[i] if canonical_batch is not None else content_items[i]
                ),
            )

            cache_key = self._batch_eval_cache_key(
                content,
                profile_digest=profile_digest,
                negative_digest=negative_digest,
                evaluation_bucket=evaluation_bucket,
                source_context=source_context,
            )
            if normal_cache_enabled and i in recall.complete_indices:
                self._set_eval_cache_entry(
                    cache_key,
                    _eval_cache_entry_for_content(content),
                )
            results.append(score)

        return results

    @staticmethod
    def _apply_intra_batch_caps(
        batch: Sequence[DiscoveredContent],
        results: list[float] | list[float | None],
    ) -> None:
        """Apply batch-dependent diversity caps to raw or cached eval scores.

        Eval-cache entries intentionally store the model's raw per-item result.
        These caps depend on the current sibling batch, so they must be applied
        after every cache lookup as well as after a fresh provider response.
        """

        # v0.3.50+: intra-batch franchise cap. The LLM dutifully fills
        # franchise_key for IP/series content (per the prompt's batch-
        # consistency rule), but we used to keep all 30 items even when
        # ≥10 of them shared a franchise — observed in production:
        # 张雪机车×13 / 风犬少年的天空×7 / 咲间妮娜×7 in single batches.
        # Cap at ``_BATCH_FRANCHISE_CAP`` per batch: keep the highest-
        # scoring N items per franchise, zero the rest. Empty franchise
        # is exempt (most generic content has no IP signal).
        cap = _BATCH_FRANCHISE_CAP
        if cap > 0 and batch:
            buckets: dict[str, list[int]] = {}
            for i, content in enumerate(batch):
                capped_score = results[i] if i < len(results) else None
                if capped_score is None or capped_score < 0.5:
                    continue
                key = (content.franchise_key or "").strip().lower()
                if not key:
                    continue
                buckets.setdefault(key, []).append(i)
            dropped = 0
            for _key, indices in buckets.items():
                if len(indices) <= cap:
                    continue
                # Keep top ``cap`` by score, drop the rest.
                indices.sort(key=lambda idx: results[idx] or 0.0, reverse=True)
                for idx in indices[cap:]:
                    results[idx] = 0.0
                    batch[idx].relevance_score = 0.0
                    batch[idx].relevance_reason = ""
                    dropped += 1
            if dropped:
                logger.info(
                    "eval_batch franchise cap: dropped %d item(s) (cap=%d/franchise; offenders=%s)",
                    dropped,
                    cap,
                    ", ".join(f"{k}×{len(v)}" for k, v in buckets.items() if len(v) > cap),
                )

        # v0.3.51+: same-style cap (mirrors v0.3.50 franchise cap).
        # Production logs (2026-05-05) showed single-style concentration
        # 7-12/30 in many eval batches (mood_release×10,
        # story_immersion×11, social_chat×11, hands_on×10). Pool inherits this skew
        # because eval_batch keeps all 30 — diversifier at serve time
        # can't unbias a pool that's already 30%+ same-style.
        # Cap=8 (27% of a 30-batch) lets a style have a small foothold
        # but stops single-style domination of the round.
        style_cap = _BATCH_STYLE_CAP
        if style_cap > 0 and batch:
            style_buckets: dict[str, list[int]] = {}
            for i, content in enumerate(batch):
                capped_score = results[i] if i < len(results) else None
                if capped_score is None or capped_score < 0.5:
                    continue
                style_key = normalize_style_key(content.style_key)
                if not style_key:
                    continue
                style_buckets.setdefault(style_key, []).append(i)
            style_dropped = 0
            for _style_key, indices in style_buckets.items():
                if len(indices) <= style_cap:
                    continue
                indices.sort(key=lambda idx: results[idx] or 0.0, reverse=True)
                for idx in indices[style_cap:]:
                    results[idx] = 0.0
                    batch[idx].relevance_score = 0.0
                    batch[idx].relevance_reason = ""
                    style_dropped += 1
            if style_dropped:
                logger.info(
                    "eval_batch style cap: dropped %d item(s) (cap=%d/style; offenders=%s)",
                    style_dropped,
                    style_cap,
                    ", ".join(
                        f"{k}×{len(v)}" for k, v in style_buckets.items() if len(v) > style_cap
                    ),
                )

    async def _evaluate_batch(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
        *,
        source_context: str = "",
        negative_examples: object = _NEGATIVE_EXAMPLES_UNSET,
        evaluated_at: str = "",
        evaluation_bucket: str = "",
        normal_cache_enabled: bool = True,
        apply_batch_caps: bool = True,
        max_split_depth: int = 3,
        max_extra_requests: int = 6,
        valid_score_indices: set[int] | None = None,
    ) -> list[float]:
        """Retry only missing members from successful malformed responses."""
        from openbiliclaw.llm.prompts import content_evaluation_clock

        if not evaluated_at or not evaluation_bucket:
            current_evaluated_at, current_bucket = content_evaluation_clock()
            evaluated_at = evaluated_at or current_evaluated_at
            evaluation_bucket = evaluation_bucket or current_bucket
        results: list[float | None] = [None] * len(batch)
        if valid_score_indices is not None:
            valid_score_indices.clear()
        budget = {"remaining": max(0, int(max_extra_requests))}
        normal_cache_enabled = normal_cache_enabled and self._batch_normal_cache_eligible(
            batch,
            source_context=source_context,
        )

        async def run(indices: list[int], depth: int) -> None:
            subset = [batch[index] for index in indices]
            subset_results = await self._evaluate_batch_once(
                subset,
                profile,
                source_context=source_context,
                negative_examples=negative_examples,
                evaluated_at=evaluated_at,
                evaluation_bucket=evaluation_bucket,
                normal_cache_enabled=normal_cache_enabled,
            )
            missing: list[int] = []
            for index, score in zip(indices, subset_results, strict=True):
                results[index] = score
                if score is None:
                    missing.append(index)
            if not missing or depth >= max_split_depth or budget["remaining"] <= 0:
                return
            children: tuple[list[int], ...]
            if len(missing) < len(indices):
                children = (missing,)
            else:
                midpoint = max(1, len(missing) // 2)
                children = (missing[:midpoint], missing[midpoint:])
            for child in children:
                if not child or budget["remaining"] <= 0:
                    break
                budget["remaining"] -= 1
                await run(child, depth + 1)

        await run(list(range(len(batch))), 0)
        final: list[float] = []
        for index, (content, score) in enumerate(zip(batch, results, strict=True)):
            if score is None:
                content.relevance_reason = "evaluation_response_missing"
                final.append(0.0)
            else:
                if valid_score_indices is not None:
                    valid_score_indices.add(index)
                final.append(score)
        if apply_batch_caps:
            self._apply_intra_batch_caps(batch, final)
        return final

    @staticmethod
    def _clamp_score(raw_value: object) -> float:
        if isinstance(raw_value, bool | int | float):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            try:
                value = float(raw_value)
            except ValueError:
                value = 0.0
        else:
            value = 0.0
        return max(0.0, min(1.0, round(value, 4)))

    @classmethod
    def _validated_model_score(cls, raw_value: object) -> float | None:
        """Return a production-valid evaluator score without inventing zero."""

        if isinstance(raw_value, bool):
            return None
        if isinstance(raw_value, int | float):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            try:
                value = float(raw_value)
            except ValueError:
                return None
        else:
            return None
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return None
        return cls._clamp_score(value)

    @staticmethod
    def _merge_duplicates(results: list[DiscoveredContent]) -> list[DiscoveredContent]:
        by_identity: dict[str, DiscoveredContent] = {}
        for item in results:
            identity = ContentDiscoveryEngine._content_identity(item)
            existing = by_identity.get(identity)
            if existing is None or item.relevance_score > existing.relevance_score:
                by_identity[identity] = item
        return list(by_identity.values())

    @staticmethod
    def _content_identity(item: DiscoveredContent) -> str:
        platform = (item.source_platform or "bilibili").strip() or "bilibili"
        content_id = (item.content_id or item.bvid or item.content_url).strip()
        if content_id:
            return f"{platform}:{content_id}"
        return f"{platform}:title:{item.title}:{item.author_name or item.up_name}"

    async def _run_strategies(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
        fully_parallel: bool = False,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
        keywords: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
    ) -> list[DiscoveredContent]:
        results: list[DiscoveredContent] = []
        run_entries = [
            (strategy, self._strategy_run_limit(strategy, limit, strategy_limits))
            for strategy in strategies
        ]
        run_entries = [
            (strategy, run_limit) for strategy, run_limit in run_entries if run_limit > 0
        ]
        if not run_entries:
            return []

        if fully_parallel:
            # One shot: every strategy runs in a single gather. We rely
            # on ``bilibili_request_concurrency`` + ``search_budget_total``
            # to bound IP-level pressure; the default phase split is
            # safer but adds ~search_wall_time before others start.
            names = [s.name for s, _ in run_entries]
            logger.info("discover start (fully_parallel): strategies=%s limit=%d", names, limit)
            t0 = time.monotonic()

            async def _timed(
                strategy: DiscoveryStrategy,
                run_limit: int,
            ) -> list[DiscoveredContent]:
                s_t0 = time.monotonic()
                logger.info("strategy %s: dispatch limit=%d", strategy.name, run_limit)
                try:
                    result = await _call_strategy_discover(
                        strategy,
                        profile,
                        limit=run_limit,
                        pool_snapshot=pool_snapshot,
                        keywords=keywords,
                        keyword_ids=keyword_ids,
                    )
                finally:
                    logger.info(
                        "strategy %s: done in %.1fs",
                        strategy.name,
                        time.monotonic() - s_t0,
                    )
                return result

            gathered = await asyncio.gather(
                *(_timed(s, run_limit) for s, run_limit in run_entries),
                return_exceptions=True,
            )
            results.extend(self._collect_strategy_results([s for s, _ in run_entries], gathered))
            logger.info(
                "discover done (fully_parallel): strategies=%s total_elapsed=%.1fs results=%d",
                names,
                time.monotonic() - t0,
                len(results),
            )
        else:
            # Split strategies into two phases to avoid B站 IP-level
            # search rate-limiting. Search runs first (Phase 1) with a
            # dedicated cookie-free client so it gets clean quota.
            # Other strategies (explore, related_chain) also call the
            # search API, so each strategy's calls are capped by the
            # per-strategy search budget in
            # ``DiscoveryConcurrencyController``.
            search_entries = [(s, run_limit) for s, run_limit in run_entries if s.name == "search"]
            other_entries = [(s, run_limit) for s, run_limit in run_entries if s.name != "search"]

            # Phase 1: run search strategy first to get clean IP quota
            if search_entries:
                tasks = [
                    _call_strategy_discover(
                        s,
                        profile,
                        limit=run_limit,
                        pool_snapshot=pool_snapshot,
                        keywords=keywords,
                        keyword_ids=keyword_ids,
                    )
                    for s, run_limit in search_entries
                ]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend(
                    self._collect_strategy_results([s for s, _ in search_entries], gathered)
                )

            # Brief cooldown between phases to let IP-level rate limit recover
            if search_entries and other_entries:
                await asyncio.sleep(2.0)

            # Phase 2: run remaining strategies concurrently
            if other_entries:
                tasks = [
                    _call_strategy_discover(
                        s,
                        profile,
                        limit=run_limit,
                        pool_snapshot=pool_snapshot,
                    )
                    for s, run_limit in other_entries
                ]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend(
                    self._collect_strategy_results([s for s, _ in other_entries], gathered)
                )

        logger.info(
            "Discovery gather returned %d results for %d strategies: %s",
            len(results),
            len(run_entries),
            [s.name for s, _ in run_entries],
        )
        return results

    @staticmethod
    def _strategy_run_limit(
        strategy: DiscoveryStrategy,
        default_limit: int,
        strategy_limits: dict[str, int] | None,
    ) -> int:
        if not strategy_limits:
            return max(1, int(default_limit))
        raw_limit = strategy_limits.get(strategy.name, default_limit)
        try:
            run_limit = int(raw_limit)
        except (TypeError, ValueError):
            run_limit = default_limit
        return max(0, min(max(1, int(default_limit)), run_limit))

    @staticmethod
    def _collect_strategy_results(
        strategies: list[DiscoveryStrategy],
        gathered: Sequence[list[DiscoveredContent] | BaseException],
    ) -> list[DiscoveredContent]:
        results: list[DiscoveredContent] = []
        for strategy, outcome in zip(strategies, gathered, strict=True):
            if isinstance(outcome, BaseException):
                logger.exception(
                    "Strategy '%s' failed: %s: %s",
                    strategy.name,
                    type(outcome).__name__,
                    outcome,
                    exc_info=outcome,
                )
                continue
            if not isinstance(outcome, list):
                logger.error(
                    "Strategy '%s' returned unexpected outcome type: %s",
                    strategy.name,
                    type(outcome).__name__,
                )
                continue
            items: list[DiscoveredContent] = outcome
            results.extend(items)
            # v0.3.31+: per-strategy raw diversity snapshot. Items at
            # this point are pre-LLM-evaluation (topic_group / style_key
            # not set yet), so we report what's observable: title-level
            # uniqueness, up_name spread, and platform mix. Catches
            # "search returned 13 items but they're all from the same UP
            # / all same title prefix" pathologies.
            ups: Counter[str] = Counter((c.up_name or "").strip().lower() for c in items)
            del ups[""]
            unique_titles = len({c.title.strip() for c in items if c.title})
            platforms: Counter[str] = Counter((c.source_platform or "bilibili") for c in items)
            top_up = ups.most_common(1)[0] if ups else ("", 0)
            logger.info(
                "Strategy '%s' found %d items.%s "
                "diversity={unique_titles=%d/%d, unique_ups=%d, top_up=%s×%d, platforms=%s}",
                strategy.name,
                len(items),
                "" if items else " (empty — all candidates filtered or generation failed)",
                unique_titles,
                len(items) or 1,
                len(ups),
                top_up[0] or "—",
                top_up[1],
                dict(platforms.most_common()),
            )
        return results

    async def _run_backfill(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
        existing: list[DiscoveredContent],
        pool_snapshot: Any | None = None,
    ) -> list[DiscoveredContent]:
        remaining = limit - len(existing)
        if remaining <= 0:
            return []

        backfill_strategies: list[DiscoveryStrategy | None] = []
        for strategy in strategies:
            factory = getattr(strategy, "create_backfill_strategy", None)
            if not callable(factory):
                backfill_strategies.append(None)
                continue
            backfill_strategies.append(factory())
        active_backfill = [strategy for strategy in backfill_strategies if strategy is not None]
        results: list[DiscoveredContent] = []
        if active_backfill:
            results.extend(
                await self._run_strategies(
                    active_backfill,
                    profile=profile,
                    limit=remaining,
                    pool_snapshot=pool_snapshot,
                )
            )

        merged = self._merge_and_rank([*existing, *results])[:limit]
        if len(merged) >= limit:
            return results

        results.extend(
            self._load_cached_backfill(
                limit=limit,
                exclude_bvids={item.bvid for item in merged},
                source_platforms={
                    normalize_source_platform(
                        getattr(strategy, "source_platform", "bilibili"),
                        default="bilibili",
                    )
                    for strategy in strategies
                },
            )
        )
        return results

    def _load_cached_backfill(
        self,
        *,
        limit: int,
        exclude_bvids: set[str],
        source_platforms: set[str],
    ) -> list[DiscoveredContent]:
        if self._database is None:
            return []

        rows = self._database.get_unrecommended_content(
            limit=limit,
            source_platforms=sorted(source_platforms),
        )
        candidates: list[DiscoveredContent] = []
        for row in rows:
            bvid = str(row.get("bvid", "")).strip()
            if not bvid or bvid in exclude_bvids:
                continue
            candidates.append(
                DiscoveredContent(
                    bvid=bvid,
                    title=str(row.get("title", "")),
                    up_name=str(row.get("up_name", "")),
                    up_mid=int(row.get("up_mid", 0) or 0),
                    duration=int(row.get("duration", 0) or 0),
                    tags=[],
                    topic_key=str(row.get("topic_key", "")),
                    topic_group=str(row.get("topic_group", "")),
                    style_key=str(row.get("style_key", "")),
                    description=str(row.get("description", "")),
                    published_at=str(row.get("published_at", "") or ""),
                    published_label=str(row.get("published_label", "") or ""),
                    cover_url=str(row.get("cover_url", "")),
                    view_count=int(row.get("view_count", 0) or 0),
                    like_count=int(row.get("like_count", 0) or 0),
                    source_strategy=str(row.get("source", "")),
                    relevance_score=self._clamp_score(row.get("relevance_score", 0.0)),
                    relevance_reason=str(row.get("relevance_reason", "")),
                    temporal_class=str(row.get("temporal_class", "unknown") or "unknown"),
                    temporal_confidence=float(row.get("temporal_confidence", 0.0) or 0.0),
                    temporal_reason=str(row.get("temporal_reason", "") or ""),
                    temporal_policy_version=str(
                        row.get("temporal_policy_version", TEMPORAL_POLICY_VERSION)
                        or TEMPORAL_POLICY_VERSION
                    ),
                    temporal_validity_mode=str(row.get("temporal_validity_mode", "none") or "none"),
                    temporal_valid_until=str(row.get("temporal_valid_until", "") or ""),
                    temporal_scope=str(row.get("temporal_scope", "none") or "none"),
                    temporal_evidence=str(row.get("temporal_evidence", "") or ""),
                    temporal_state=str(row.get("temporal_state", "unknown") or "unknown"),
                    temporal_next_review_at=str(row.get("temporal_next_review_at", "") or ""),
                    temporal_evaluated_at=str(row.get("temporal_evaluated_at", "") or ""),
                    temporal_evidence_complete=is_complete_temporal_evidence_marker(
                        row.get("temporal_evidence_complete")
                    ),
                    pool_expression=str(row.get("pool_expression", "") or ""),
                    pool_topic_label=str(row.get("pool_topic_label", "") or ""),
                    candidate_tier="backfill",
                    discovered_at=str(row.get("discovered_at", "")),
                    last_scored_at=str(row.get("last_scored_at", "")),
                    content_id=str(row.get("content_id", "") or bvid),
                    content_url=str(row.get("content_url", "")),
                    source_platform=str(row.get("source_platform", "") or "bilibili"),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    @staticmethod
    def _merge_and_rank(results: list[DiscoveredContent]) -> list[DiscoveredContent]:
        merged = ContentDiscoveryEngine._merge_duplicates(results)
        merged.sort(
            key=lambda item: (
                item.candidate_tier != "primary",
                -item.relevance_score,
                -item.view_count,
                item.bvid,
            )
        )
        return merged

    @staticmethod
    def _apply_pool_snapshot_rerank(
        results: list[DiscoveredContent],
        pool_snapshot: Any | None,
    ) -> list[DiscoveredContent]:
        if pool_snapshot is None or len(results) <= 1:
            return list(results)

        saturated_topics = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "saturated_topics",
        )
        saturated_styles = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "saturated_styles",
        )
        saturated_franchises = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "saturated_franchises",
        )
        undercovered_axes = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "undercovered_axes",
        )
        if not (saturated_topics or saturated_styles or saturated_franchises or undercovered_axes):
            return list(results)

        indexed_results = list(enumerate(results))
        indexed_results.sort(
            key=lambda indexed: ContentDiscoveryEngine._pool_rerank_key(
                indexed[1],
                original_index=indexed[0],
                saturated_topics=saturated_topics,
                saturated_styles=saturated_styles,
                saturated_franchises=saturated_franchises,
                undercovered_axes=undercovered_axes,
            )
        )
        return [item for _, item in indexed_results]

    @staticmethod
    def _pool_rerank_key(
        item: DiscoveredContent,
        *,
        original_index: int,
        saturated_topics: set[str],
        saturated_styles: set[str],
        saturated_franchises: set[str],
        undercovered_axes: set[str],
    ) -> tuple[bool, bool, float, float, int]:
        raw_score = item.relevance_score
        adjusted_score = raw_score
        topic = ContentDiscoveryEngine._topic_bucket(item)
        style = ContentDiscoveryEngine._style_bucket(item)
        franchise = ContentDiscoveryEngine._normalize_topic_token(item.franchise_key)

        if topic in saturated_topics:
            adjusted_score -= 0.08
        if style in saturated_styles:
            adjusted_score -= 0.04
        if franchise in saturated_franchises:
            adjusted_score -= 0.10
        if topic in undercovered_axes:
            adjusted_score += 0.04

        return (
            item.candidate_tier != "primary",
            raw_score < 0.92,
            -adjusted_score,
            -raw_score,
            original_index,
        )

    @staticmethod
    def _normalized_snapshot_values(pool_snapshot: Any, attribute: str) -> set[str]:
        values = getattr(pool_snapshot, attribute, ()) or ()
        if not isinstance(values, (list, tuple, set, frozenset)):
            return set()
        if attribute == "saturated_styles":
            return {
                token
                for value in values
                if isinstance(value, str)
                if (
                    token := ContentDiscoveryEngine._normalize_topic_token(
                        normalize_style_key(value)
                    )
                )
            }
        return {
            token
            for value in values
            if isinstance(value, str)
            if (token := ContentDiscoveryEngine._normalize_topic_token(value))
        }

    @staticmethod
    def _compress_topic_repeats(
        results: list[DiscoveredContent],
        *,
        limit: int,
    ) -> list[DiscoveredContent]:
        if limit <= 1 or len(results) <= 1:
            return results[:limit]

        per_style_cap = ContentDiscoveryEngine._style_cap(limit)
        per_source_cap = ContentDiscoveryEngine._source_cap(limit)
        unique_sources = {
            ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            for item in results
            if ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
        }
        unique_source_target = min(limit, len(unique_sources))

        # Step 0: reserve minimum slots per source strategy.
        # Without a floor, high-scoring sources (related_chain) monopolize all
        # slots via the score-sorted selection, leaving low-scoring but novel
        # sources (search, explore) with zero representation.
        n_sources = max(1, len(unique_sources))
        per_source_floor = max(1, limit // n_sources) if unique_sources else 0
        # Hard ceiling: no single source takes more than ~35% of results,
        # even if it has unlimited topic diversity (e.g. trending).
        per_source_ceiling = max(per_source_floor + 1, limit * 35 // 100)
        reserved, unreserved = ContentDiscoveryEngine._reserve_per_source(
            results,
            per_source_floor=per_source_floor,
            unique_sources=unique_sources,
        )

        # Step 1: select diverse subset from unreserved pool.
        # Pass reserved items' topics/sources so _select_diverse knows what
        # has already been committed.
        remaining_limit = limit - len(reserved)
        reserved_topics = {ContentDiscoveryEngine._topic_bucket(i) for i in reserved} - {""}
        reserved_sources = {
            ContentDiscoveryEngine._normalize_topic_token(i.source_strategy) for i in reserved
        } - {""}
        selected, overflow = ContentDiscoveryEngine._select_diverse(
            unreserved,
            limit=remaining_limit,
            per_style_cap=per_style_cap,
            per_source_cap=max(1, per_source_cap - per_source_floor),
            unique_source_target=unique_source_target,
            initial_seen_topics=reserved_topics,
            initial_seen_sources=reserved_sources,
        )

        # Combine reserved + selected
        combined = list(reserved)
        reserved_keys = {ContentDiscoveryEngine._content_identity(item) for item in reserved}
        for item in selected:
            if ContentDiscoveryEngine._content_identity(item) not in reserved_keys:
                combined.append(item)
        if len(combined) >= limit:
            return combined[:limit]

        # Step 2: backfill from overflow with relaxed constraints
        combined = ContentDiscoveryEngine._backfill_from_overflow(
            combined,
            overflow,
            limit=limit,
            per_style_cap=per_style_cap,
            per_source_cap=per_source_cap,
            per_source_ceiling=per_source_ceiling,
        )
        return combined[:limit]

    @staticmethod
    def _reserve_per_source(
        results: list[DiscoveredContent],
        *,
        per_source_floor: int,
        unique_sources: set[str],
    ) -> tuple[list[DiscoveredContent], list[DiscoveredContent]]:
        """Reserve the best items from each source to guarantee representation.

        Returns (reserved, unreserved) where reserved contains at most
        *per_source_floor* items per source (the highest-scored ones),
        and unreserved contains everything else.
        """
        if per_source_floor <= 0:
            return [], list(results)

        source_buckets: dict[str, list[DiscoveredContent]] = {s: [] for s in unique_sources}
        for item in results:
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if source in source_buckets:
                source_buckets[source].append(item)

        reserved: list[DiscoveredContent] = []
        reserved_keys: set[str] = set()
        # Track topics across ALL sources to avoid reserving duplicate topics
        global_seen_topics: set[str] = set()
        source_counts: dict[str, int] = {s: 0 for s in unique_sources}

        # Round-robin: iterate by score across all sources, reserving items
        # until each source reaches its floor.  Skip items whose topic is
        # already reserved (from any source) to maximise topic diversity.
        for item in results:
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if source not in source_counts or source_counts[source] >= per_source_floor:
                continue
            topic = ContentDiscoveryEngine._topic_bucket(item)
            if topic and topic in global_seen_topics:
                continue
            reserved.append(item)
            reserved_keys.add(ContentDiscoveryEngine._content_identity(item))
            source_counts[source] += 1
            if topic:
                global_seen_topics.add(topic)

        unreserved = [
            item
            for item in results
            if ContentDiscoveryEngine._content_identity(item) not in reserved_keys
        ]
        return reserved, unreserved

    @staticmethod
    def _select_diverse(
        results: list[DiscoveredContent],
        *,
        limit: int,
        per_style_cap: int,
        per_source_cap: int,
        unique_source_target: int,
        initial_seen_topics: set[str] | None = None,
        initial_seen_sources: set[str] | None = None,
    ) -> tuple[list[DiscoveredContent], list[DiscoveredContent]]:
        """Select a diverse subset, deferring duplicates to overflow."""
        selected: list[DiscoveredContent] = []
        overflow: list[DiscoveredContent] = []
        seen_topics: set[str] = set(initial_seen_topics or ())
        seen_sources: set[str] = set(initial_seen_sources or ())
        style_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}

        for item in results:
            topic = ContentDiscoveryEngine._topic_bucket(item)
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            is_new_source = (
                bool(source)
                and source not in seen_sources
                and len(seen_sources) < unique_source_target
            )

            if topic and topic in seen_topics:
                overflow.append(item)
                continue
            if not is_new_source and style and style_counts.get(style, 0) >= per_style_cap:
                overflow.append(item)
                continue
            if source and source_counts.get(source, 0) >= per_source_cap:
                overflow.append(item)
                continue
            # Prioritize source representation: defer items from already-seen
            # sources until all unique sources have at least one entry.
            if (
                not is_new_source
                and source
                and source in seen_sources
                and len(seen_sources) < unique_source_target
            ):
                overflow.append(item)
                continue

            selected.append(item)
            if topic:
                seen_topics.add(topic)
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                seen_sources.add(source)
                source_counts[source] = source_counts.get(source, 0) + 1
            if len(selected) >= limit:
                break

        return selected, overflow

    @staticmethod
    def _backfill_from_overflow(
        selected: list[DiscoveredContent],
        overflow: list[DiscoveredContent],
        *,
        limit: int,
        per_style_cap: int,
        per_source_cap: int,
        per_source_ceiling: int = 0,
    ) -> list[DiscoveredContent]:
        """Fill remaining slots from overflow with relaxed topic constraint.

        Enforces a per-topic-group cap so that no single topic_group
        dominates the final result set (max ~20% of limit), and a
        per-source ceiling so that no single source exceeds ~35%.
        """
        # Per-topic cap: no single topic_group takes more than ~20% of results.
        # For small limits (≤5) this is 1, preserving strict topic dedup.
        per_topic_cap = max(1, limit // 5)
        # Hard source ceiling: even with infinite topic diversity, a single
        # source cannot take more than this many slots in total.
        source_ceiling = (
            per_source_ceiling
            if per_source_ceiling > 0
            else max(per_source_cap + 1, limit * 35 // 100)
        )

        topic_counts: dict[str, int] = {}
        style_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        for item in selected:
            topic = ContentDiscoveryEngine._topic_bucket(item)
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 1: allow new or under-cap topics from overflow
        remaining: list[DiscoveredContent] = []
        for item in overflow:
            if len(selected) >= limit:
                break
            topic = ContentDiscoveryEngine._topic_bucket(item)
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if topic and topic_counts.get(topic, 0) >= per_topic_cap:
                remaining.append(item)
                continue
            if style and style_counts.get(style, 0) >= per_style_cap:
                remaining.append(item)
                continue
            if source and source_counts.get(source, 0) >= source_ceiling:
                remaining.append(item)
                continue
            selected.append(item)
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 2: fill remaining with soft caps (topic ≤30%, source ≤ ceiling)
        max_per_topic = max(per_topic_cap + 1, limit * 3 // 10)
        leftover: list[DiscoveredContent] = []
        for item in remaining:
            if len(selected) >= limit:
                break
            topic = ContentDiscoveryEngine._topic_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if source and source_counts.get(source, 0) >= source_ceiling:
                leftover.append(item)
                continue
            if topic and topic_counts.get(topic, 0) >= max_per_topic:
                leftover.append(item)
                continue
            selected.append(item)
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 3: truly unconditional fill if still short
        for item in leftover:
            if len(selected) >= limit:
                break
            selected.append(item)

        return selected

    @staticmethod
    def _topic_bucket(item: DiscoveredContent) -> str:
        """Use topic_group (coarse) for diversity bucketing, fall back to topic_key."""
        if item.topic_group.strip():
            return ContentDiscoveryEngine._normalize_topic_token(item.topic_group)
        if item.topic_key.strip():
            return ContentDiscoveryEngine._normalize_topic_token(item.topic_key)
        for tag in item.tags:
            token = ContentDiscoveryEngine._normalize_topic_token(tag)
            if token:
                return token
        return ""

    @staticmethod
    def _style_bucket(item: DiscoveredContent) -> str:
        return ContentDiscoveryEngine._normalize_topic_token(normalize_style_key(item.style_key))

    @staticmethod
    def _normalize_topic_token(value: str) -> str:
        compact = re.sub(r"\s+", "", value.strip().lower())
        return compact[:32]

    @staticmethod
    def _style_cap(limit: int) -> int:
        return max(1, min(3, (limit + 1) // 3))

    @staticmethod
    def _source_cap(limit: int) -> int:
        return 2 if limit <= 5 else 3

    @staticmethod
    def infer_style_key(
        *,
        title: str,
        description: str = "",
        reason: str = "",
        source_strategy: str = "",
    ) -> str:
        from openbiliclaw.discovery.style_rules import infer_style_key as _infer

        return _infer(
            title=title,
            description=description,
            reason=reason,
            source_strategy=source_strategy,
        )

    def _cached_result_count(self, results: list[DiscoveredContent]) -> int:
        database = getattr(self, "_database", None)
        if database is None or not results:
            return 0
        keys = [item.item_key for item in results if item.item_key]
        if not keys:
            return 0
        try:
            conn = database.conn
            placeholders = ", ".join("?" for _ in keys)
            cursor = conn.execute(
                f"SELECT COUNT(*) AS count FROM content_cache WHERE item_key IN ({placeholders})",
                keys,
            )
            row = cursor.fetchone()
        except Exception:
            logger.debug("cache_evaluated_results: cached-row count unavailable", exc_info=True)
            return 0
        if row is None:
            return 0
        return int(row["count"] if isinstance(row, dict) else row[0])

    def cache_evaluated_results_detailed(
        self,
        results: list[DiscoveredContent],
    ) -> CacheEvaluatedBatchOutcome:
        """Persist evaluated results with the storage lock's final decision."""

        if self._database is None or not results:
            return CacheEvaluatedBatchOutcome()
        before = self._cached_result_count(results)
        outcomes = self._cache_results(results)
        if outcomes and all(outcome.newly_cached is not None for outcome in outcomes):
            newly_cached = sum(bool(outcome.newly_cached) for outcome in outcomes)
        else:
            after = self._cached_result_count(results)
            newly_cached = max(0, after - before)
        return CacheEvaluatedBatchOutcome(
            newly_cached=newly_cached,
            items=outcomes,
        )

    def cache_evaluated_results(self, results: list[DiscoveredContent]) -> int:
        """Persist evaluated results and return newly cached row count."""

        return self.cache_evaluated_results_detailed(results).newly_cached

    async def normalize_evaluated_results(self, results: list[DiscoveredContent]) -> None:
        """Apply discovery topic normalization before evaluated candidates are cached."""

        await self._normalize_topic_groups(results)
        await self._normalize_topic_keys(results)

    def cache_admission_block_reason(self, item: DiscoveredContent) -> str:
        """Return why an evaluated item should not be written to ``content_cache``."""

        temporal = evaluate_temporal_eligibility(
            temporal_class=item.temporal_class,
            temporal_confidence=item.temporal_confidence,
            published_at=item.published_at,
            temporal_validity_mode=item.temporal_validity_mode,
            temporal_valid_until=item.temporal_valid_until,
            temporal_scope=item.temporal_scope,
            temporal_evidence=item.temporal_evidence,
            temporal_state=item.temporal_state,
            temporal_next_review_at=item.temporal_next_review_at,
            temporal_evaluated_at=item.temporal_evaluated_at,
            temporal_policy_version=item.temporal_policy_version,
            evidence_complete=item.temporal_evidence_complete,
        )
        if temporal.hard_expired:
            return "temporal_stale"
        if temporal.needs_review:
            return "temporal_review_due"

        if self._database is None:
            return ""
        viewed_content_keys = self._recent_viewed_content_keys()
        if viewed_content_keys and not self._candidate_view_keys(item).isdisjoint(
            viewed_content_keys
        ):
            return "recently_viewed"

        franchise_key = (item.franchise_key or "").strip().lower()
        if not franchise_key or _POOL_FRANCHISE_QUOTA <= 0:
            return ""
        try:
            existing_franchise_counts = self._database.count_pool_by_franchise()
        except Exception:
            logger.debug("count_pool_by_franchise unavailable", exc_info=True)
            return ""
        if int(existing_franchise_counts.get(franchise_key, 0)) >= _POOL_FRANCHISE_QUOTA:
            return "franchise_quota"
        return ""

    def _admission_threshold_for_item(self, item: DiscoveredContent) -> float:
        database_threshold = getattr(self._database, "pool_admission_threshold", None)
        if callable(database_threshold):
            return float(
                database_threshold(
                    item.source_strategy,
                    item.score_threshold or None,
                )
            )
        return effective_admission_threshold(
            item.source_strategy,
            requested_threshold=item.score_threshold or None,
        )

    def _cache_results(
        self,
        results: list[DiscoveredContent],
    ) -> tuple[CacheEvaluatedItemOutcome, ...]:
        if self._database is None or not results:
            return ()

        # v0.3.50+: pool-wide franchise quota. Without this, multiple
        # discovery rounds can each pass the per-batch cap (4 张雪机车
        # in batch 1, 4 in batch 2, ...) and the pool ends up with 30+
        # items of the same franchise — diversifier at serve time
        # cannot rescue a pool that's already franchise-skewed.
        existing_franchise_counts: dict[str, int] = {}
        if _POOL_FRANCHISE_QUOTA > 0:
            try:
                existing_franchise_counts = self._database.count_pool_by_franchise()
            except Exception:
                # Old DB or test stub without the helper — skip the
                # quota check rather than fail caching entirely.
                logger.debug("count_pool_by_franchise unavailable", exc_info=True)
                existing_franchise_counts = {}

        persisted: list[DiscoveredContent] = []
        skipped_franchise: dict[str, int] = {}
        skipped_viewed = 0
        skipped_low_score = 0
        skipped_temporal_stale = 0
        outcomes: list[CacheEvaluatedItemOutcome] = []
        round_franchise_counts: dict[str, int] = {}
        viewed_content_keys = self._recent_viewed_content_keys()
        for item in results:
            temporal = evaluate_temporal_eligibility(
                temporal_class=item.temporal_class,
                temporal_confidence=item.temporal_confidence,
                published_at=item.published_at,
                temporal_validity_mode=item.temporal_validity_mode,
                temporal_valid_until=item.temporal_valid_until,
                temporal_scope=item.temporal_scope,
                temporal_evidence=item.temporal_evidence,
                temporal_state=item.temporal_state,
                temporal_next_review_at=item.temporal_next_review_at,
                temporal_evaluated_at=item.temporal_evaluated_at,
                temporal_policy_version=item.temporal_policy_version,
                evidence_complete=item.temporal_evidence_complete,
            )
            if not temporal.eligible:
                skipped_temporal_stale += 1
                outcomes.append(
                    CacheEvaluatedItemOutcome(
                        bvid=item.bvid,
                        admitted=False,
                        newly_cached=False,
                        temporal_rejection_reason=(
                            temporal.rejection_reason if temporal.hard_expired else ""
                        ),
                        temporal_review_reason=(temporal.reason if temporal.needs_review else ""),
                    )
                )
                continue
            if viewed_content_keys and not self._candidate_view_keys(item).isdisjoint(
                viewed_content_keys
            ):
                skipped_viewed += 1
                outcomes.append(
                    CacheEvaluatedItemOutcome(
                        bvid=item.bvid,
                        admitted=False,
                        newly_cached=False,
                    )
                )
                continue
            if float(item.relevance_score or 0.0) < self._admission_threshold_for_item(item):
                skipped_low_score += 1
                outcomes.append(
                    CacheEvaluatedItemOutcome(
                        bvid=item.bvid,
                        admitted=False,
                        newly_cached=False,
                    )
                )
                continue
            franchise_key = (item.franchise_key or "").strip().lower()
            if franchise_key and _POOL_FRANCHISE_QUOTA > 0:
                pool_existing = existing_franchise_counts.get(franchise_key, 0)
                round_existing = round_franchise_counts.get(franchise_key, 0)
                if pool_existing + round_existing >= _POOL_FRANCHISE_QUOTA:
                    skipped_franchise[franchise_key] = skipped_franchise.get(franchise_key, 0) + 1
                    outcomes.append(
                        CacheEvaluatedItemOutcome(
                            bvid=item.bvid,
                            admitted=False,
                            newly_cached=False,
                        )
                    )
                    continue
            try:
                storage_key = content_storage_key(
                    item.source_platform,
                    item.content_id or item.bvid,
                    item.content_url,
                )
                write_result = self._database.cache_content(
                    storage_key,
                    **item.to_cache_kwargs(),
                )
                if hasattr(write_result, "admitted"):
                    admitted = bool(write_result.admitted)
                    newly_cached: bool | None = bool(write_result.created and admitted)
                    write_decision = getattr(write_result, "temporal_decision", None)
                    write_hard_expired = bool(getattr(write_decision, "hard_expired", False))
                    write_needs_review = bool(getattr(write_decision, "needs_review", False))
                    temporal_rejection_reason = (
                        str(getattr(write_decision, "rejection_reason", "") or "")
                        if write_hard_expired
                        else ""
                    )
                    temporal_review_reason = (
                        str(getattr(write_decision, "reason", "") or "")
                        if write_needs_review
                        else ""
                    )
                else:
                    # Compatibility for old/test/third-party adapters whose
                    # cache sink returns ``None``. The batch-level count delta
                    # remains the authority for ``newly_cached``.
                    admitted = True
                    newly_cached = None
                    temporal_rejection_reason = ""
                    temporal_review_reason = ""
                outcomes.append(
                    CacheEvaluatedItemOutcome(
                        bvid=item.bvid,
                        admitted=admitted,
                        newly_cached=newly_cached,
                        temporal_rejection_reason=temporal_rejection_reason,
                        temporal_review_reason=temporal_review_reason,
                    )
                )
                if not admitted:
                    if temporal_rejection_reason:
                        skipped_temporal_stale += 1
                    continue
                persisted.append(item)
                if franchise_key:
                    round_franchise_counts[franchise_key] = (
                        round_franchise_counts.get(franchise_key, 0) + 1
                    )
                # P1.8 yield backfill — the ONE admission convergence. Every
                # admitted pool item (inline-admit B站/抖音 here, and the shared
                # candidate-pipeline X/YT/XHS/抖音 path which also funnels through
                # ``cache_evaluated_results`` → ``_cache_results``) credits the
                # keyword that produced it, idempotent on (keyword, content).
                # Skipped (viewed / franchise-quota) items never reach here, so
                # they correctly accrue no yield.
                self._backfill_keyword_yield(item)
            except Exception:
                logger.exception("Failed to cache discovered content: %s", item.bvid)
                outcomes.append(
                    CacheEvaluatedItemOutcome(
                        bvid=item.bvid,
                        admitted=False,
                        newly_cached=False,
                    )
                )

        if skipped_viewed:
            logger.info(
                "pool cache skipped %d recently viewed item(s) before writing content_cache",
                skipped_viewed,
            )

        if skipped_low_score:
            logger.info(
                "pool cache skipped %d item(s) below effective admission threshold",
                skipped_low_score,
            )

        if skipped_temporal_stale:
            logger.info(
                "pool cache skipped %d temporally stale item(s) before writing content_cache",
                skipped_temporal_stale,
            )

        if skipped_franchise:
            logger.info(
                "pool franchise quota: skipped %d item(s) (cap=%d/franchise; %s)",
                sum(skipped_franchise.values()),
                _POOL_FRANCHISE_QUOTA,
                ", ".join(f"{k}×{v}" for k, v in skipped_franchise.items()),
            )

        # v0.3.45+: warm the recommendation MMR embedding cache while we
        # still hold these items in memory. Without this hook, the first
        # ``serve()`` after a discovery run pays ~150ms × N for serial
        # API calls — the warm path is L2 SQLite so subsequent reshuffles
        # are <1s. Fired in a detached task so we don't block discovery
        # finalization on a slow embedding provider.
        if persisted and self._embedding_service is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # _cache_results is sometimes called from sync test paths;
                # fall through silently rather than raise.
                return tuple(outcomes)
            loop.create_task(self._warm_mmr_embeddings(persisted))
            loop.create_task(self._warm_cover_embeddings(persisted))
        return tuple(outcomes)

    def _backfill_keyword_yield(self, item: DiscoveredContent) -> None:
        """Credit one admitted item to its producing keyword (P1.8), if any.

        No-op when the item carries no ``source_keyword_id`` (every non-search /
        legacy / flag-off item) or when the database does not expose the yield
        DAO (old stubs). Best-effort: a yield-ledger failure must never abort an
        otherwise-successful pool admission.
        """
        keyword_id = item.source_keyword_id
        if keyword_id is None:
            return
        increment = getattr(self._database, "increment_keyword_yield", None)
        if not callable(increment):
            return
        content_id = str(item.content_id or item.bvid or "").strip()
        if not content_id:
            return
        try:
            increment(int(keyword_id), content_id)
        except Exception:
            logger.debug("keyword yield backfill failed for id=%s", keyword_id, exc_info=True)

    async def _warm_mmr_embeddings(
        self,
        items: list[DiscoveredContent],
    ) -> None:
        """Pre-warm the MMR embedding cache for newly-cached items.

        Mirrors ``RecommendationEngine._mmr_embedding_text`` so the cache
        keys line up byte-for-byte. Best-effort — never raises.
        """
        if self._embedding_service is None or not items:
            return
        embedding_service = self._embedding_service

        async def _warm(item: DiscoveredContent) -> None:
            text = (f"{item.title or ''} {(item.description or '')[:160]}").strip()[:200]
            if not text:
                return
            try:
                await embedding_service.embed(text)
            except Exception:
                logger.debug(
                    "discovery._warm_mmr_embeddings: embed failed for %s",
                    item.bvid,
                    exc_info=True,
                )

        await asyncio.gather(*(_warm(item) for item in items))

    async def _warm_cover_embeddings(
        self,
        items: list[DiscoveredContent],
    ) -> None:
        """Pre-warm image-only cover embeddings when multimodal embedding is active.

        Independent of discovery vision evaluation. Stores the vector under a
        URL-derived key (``image_embedding_cache_key_for_url``) so the delight
        hot path can look it up by cover URL alone, without re-fetching bytes.
        Best-effort — never raises; text-only / disabled configs no-op.
        """
        embedding_service = self._embedding_service
        if embedding_service is None or not items:
            return
        active = getattr(embedding_service, "image_embedding_active", None)
        if not (callable(active) and active()):
            return
        embed_image = getattr(embedding_service, "embed_image", None)
        if not callable(embed_image):
            return

        from openbiliclaw.discovery import multimodal as multimodal_mod
        from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

        max_px = int(getattr(self, "multimodal_image_max_px", 384))
        quality = int(getattr(self, "multimodal_image_quality", 72))
        timeout_seconds = int(getattr(self, "multimodal_image_timeout_seconds", 6))

        async def _warm(item: DiscoveredContent) -> None:
            cover_url = (item.cover_url or "").strip()
            if not cover_url:
                return
            try:
                prepared = await multimodal_mod.prepare_cover_bytes_for_embedding(
                    cover_url,
                    max_px=max_px,
                    quality=quality,
                    timeout_seconds=timeout_seconds,
                )
                if prepared is None:
                    return
                image_bytes, mime_type = prepared
                await embed_image(
                    image_bytes,
                    mime_type=mime_type,
                    cache_key=image_embedding_cache_key_for_url(cover_url),
                )
            except Exception:
                logger.debug(
                    "discovery._warm_cover_embeddings: embed failed for %s",
                    item.bvid,
                    exc_info=True,
                )

        await asyncio.gather(*(_warm(item) for item in items))
