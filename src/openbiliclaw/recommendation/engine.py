"""Recommendation Engine — ranking, expression, and delivery.

Handles the final stage: taking discovered content and presenting it
to the user in a warm, friend-like manner with deep personal insights.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, Protocol

from openbiliclaw.discovery.style_keys import VALID_STYLE_KEYS, normalize_style_key
from openbiliclaw.llm.base import classify_llm_failure_kind
from openbiliclaw.llm.json_utils import (
    extract_llm_json_list,
    extract_llm_json_object,
    validated_text_field,
)
from openbiliclaw.llm.prompt_cache import PromptLayerRenderCache, profile_prompt_layers
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.saved_sync.identity import content_storage_key
from openbiliclaw.soul.tone import ToneProfile, build_tone_profile
from openbiliclaw.sources.platforms import normalize_source_platform, source_family

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.recommendation.curator import PoolCurator
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry
    from openbiliclaw.soul.profile import InterestTag, SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)
_DEFAULT_EXPRESSION_BATCH_SIZE = 30
_DEFAULT_EXPRESSION_BATCH_CONCURRENCY = 2

# Cover visual alignment → delight bonus (opt-in, only when
# [llm.embedding].multimodal_enabled + a multimodal embedding model are active).
#
# CALIBRATION PROVENANCE: MEASURED 2026-07-27 against dashscope
# qwen3-vl-embedding (dim=1024). The measured quantity is exactly what this
# bonus computes — per-cover MAX cosine over the text interest anchors.
#
# Two passes, both rule-3 documented:
#  1. GENERIC-ANCHOR pass (scripts/calibrate_visual_thresholds.py, 452 real
#     covers vs 8 generic interest strings): p5=0.183 p25=0.259 p50=0.302
#     p75=0.347 p95=0.403 p99=0.443 p100=0.448. This retired the original
#     0.15/0.45 guess (floor below p5 → ~97% earned bonus; ceil above p100 →
#     none reached full).
#  2. LIVE-ANCHOR pass (the production pool, 834 covers vs the user's ACTUAL
#     soul-profile interest anchors): p5=0.195 p25=0.292 p50=0.350
#     p75=0.408 p95=0.479 p99=0.541 p100=0.598. The distribution sits ~0.05
#     higher than the generic pass because the user's own interest anchors are
#     more on-style for a pool discovered for that user than 8 generic strings.
#     The constants below come from THIS pass — the live distribution is what
#     serve() actually computes, so it is the ground truth, not the generic
#     proxy.
#
# Floor at p50 means ~50% of covers earn some bonus and ~5% reach the cap,
# restoring the discrimination the generic-pass constants (0.30/0.40) had lost
# on the live pool: with those, 71% of covers earned a bonus and 28% were
# pinned at the 0.05 cap, so the top of the ranking collapsed into a flat band
# of 0.88-rel + 0.05-cover ties. Kept small + additive + one-directional;
# exactly 0 when image embedding is inactive. Reopen after any embedding
# provider/model swap (CLAUDE.md pitfall rule 3): rerun the script with
# --reset, and re-derive floor/ceil from the live pool's percentiles once it
# has grown.
_VISUAL_COVER_BONUS_MAX = 0.05  # hard cap on the additive nudge to delight_score
_VISUAL_COVER_SIM_FLOOR = 0.35  # live p50 of per-cover max anchor cosine
_VISUAL_COVER_SIM_CEIL = 0.48  # live p95 of per-cover max anchor cosine
_VISUAL_COVER_MAX_ANCHORS = 8  # profile interest anchors compared per run
# Fairness guard for the serve() hot path: right after a user enables
# multimodal, pool content admitted BEFORE the switch has no warmed cover
# vector yet (serve() is lookup-only and never fetches). Applying the bonus to
# only the warmed subset would systematically favour freshly-discovered items
# over older ones — not because their covers are better, but because the old
# ones simply aren't embedded yet. So serve() withholds the bonus for the whole
# batch until at least this fraction of cover-bearing candidates are warmed; the
# background pool-cover prewarm backfills the rest within a refresh cycle. Delight
# is unaffected (it cold-fetches per candidate, so every item gets its true bonus).
_VISUAL_COVER_MIN_COVERAGE = 0.6


# User visual-profile bonus (P1) — INDEPENDENT of the cover↔text-anchor bonus
# above. Compares a candidate cover against the user's OWN liked/disliked cover
# centroids (same-modal image↔image cosine), not text interest anchors. Runs in
# parallel and is added on top of the cover↔text bonus in serve().
#
# SCORING: margin-based, not absolute floor/ceil. The cover modality cannot
# distinguish like/dislike everywhere — measured (2026-07-28, see below),
# the user's 2 pos and 3 neg centroids are VISUALLY INTERLEAVED: 2/6 pos×neg
# pairs are "contested" (cosine 0.674 and 0.576, >= 0.40). In a contested region
# a like and a dislike point at the same cover style (love-hate), so the cover
# has no say. We abstain there. Where the cover DOES separate (s_pos - s_neg
# beyond a margin), we boost if the candidate leans liked, suppress if it leans
# disliked. One number on the difference, not two floor/ceil on absolutes —
# self-calibrating because s_pos and s_neg share one embedding/pipeline (the
# 0.80 lesson generalized: absolute thresholds depend on distribution intuition;
# a difference under the same yardstick does not).
#
# CALIBRATION PROVENANCE: MEASURED 2026-07-28 against dashscope
# qwen3-vl-embedding (dim=1024) via scripts/measure_visual_profile_geometry.py
# on the live pool (2 pos + 3 neg centroids, 1366 candidate covers):
#   contested pos×neg pairs: pos0×neg0=0.674, pos1×neg1=0.576 (>= 0.40)
#   net (s_pos - s_neg): p5=-0.154 p25=-0.075 p50=-0.024 p75=0.029 p90=0.078
#                         p95=0.114 p99=0.187   (61% of candidates net < 0)
# margin 0.05 sits between p75 (0.029) and p90 (0.078) → only the clearly
# separated top ~20% boost and bottom ~35% suppress; the contested middle
# grays out. boost scale from net p95 (0.114), suppress scale from net p5
# (-0.154). Reopen after any embedding provider/model swap OR once the
# centroid set grows: rerun the geometry script and re-derive (rule 3).
_VISUAL_PROFILE_BOOST_MAX = 0.05  # signed bonus cap when candidate clearly leans liked
_VISUAL_PROFILE_SUPPRESS_MAX = 0.08  # signed demotion cap when clearly leans disliked
_VISUAL_PROFILE_MARGIN = 0.05  # |s_pos - s_neg| at/above which the cover has an opinion
# pos×neg centroid cosine at/above which the pair is "contested" (love-hate:
# the cover modality cannot distinguish like/dislike in this region → gray).
# CALIBRATION: the 6 live pos×neg centroid pairs split into a high cluster
# (0.674, 0.576 — genuinely overlapping) and a low cluster (0.377, 0.327,
# 0.318, 0.219 — separated), with a clean gap between 0.377 and 0.576. 0.45
# sits in that gap: it flags the 2 true overlaps and leaves the 4 separated
# pairs active. (0.40 was too low — centroid means are more cohesive than raw
# covers, so 0.40 tripped almost every pair and grayed P3 out entirely.)
_VISUAL_PROFILE_CONTESTED = 0.45
# Margin required to act in a CONTESTED region (love-hate: centroids overlap,
# so the cover is less trustworthy there). 2x the normal margin: a candidate
# must clear a higher bar to boost/suppress, but a clear win still speaks
# (graying clear wins just because centroids overlap threw away ~40% of P3).
_VISUAL_PROFILE_CONTESTED_MARGIN = 0.10
# Net (s_pos - s_neg) scale endpoints for the boost/suppress mapping. From the
# live measurement: p95=0.114 (boost full-strength), p5=-0.154 (suppress full).
_VISUAL_PROFILE_NET_P95 = 0.114
_VISUAL_PROFILE_NET_P5 = -0.154
# Legacy single-signal caps retained for reference / snapshot stats; the margin
# design supersedes the absolute floor/ceil nudges below.
_VISUAL_PROFILE_PENALTY_MAX = 0.05  # UNUSED (margin scoring, see _visual_profile_bonus_from_vec)


# Video keyframe bonus (P3) — INDEPENDENT of both bonuses above. Compares a
# candidate's actual video frames (Bilibili's pre-generated videoshot sprites)
# against the SAME user visual centroids P1 built, so the taste profile matches
# what the video really looks like instead of an UP-chosen marketing cover.
# Frame vectors are max-pooled: "does any sampled frame look like the user's
# taste" is the right question for recall, and max is more sensitive to that
# than a mean that washes out a single strong match.
#
# SCORING: same margin design as P1 (boost/suppress/gray on s_pos - s_neg, with
# contested-pair abstention) — see _VISUAL_PROFILE_* above. P3 reuses P1's
# centroids and the same geometry, so it shares the contested-set and margin.
#
# CALIBRATION PROVENANCE: MEASURED 2026-07-28 against dashscope
# qwen3-vl-embedding (dim=1024) — 99 Bilibili pool candidates with real
# videoshot sprite crops, max-pooled best cosine against the P1 pos/neg
# centroids (scripts/prewarm_and_measure_keyframes.py):
#   net (pos-neg): p5=-0.081 p25=-0.018 p50=+0.022 p75=0.052 p90=0.069
#                  p95=0.093 p99=0.258   overlap(neg>pos)=41%
# P3 is marginally better than P1 at separating liked/disliked (net p50 +0.022
# vs P1's -0.024) — keyframes discriminate a little better than covers, but the
# interleaving still costs ~40% of candidates to the gray band. boost scale from
# net p95 (0.093), suppress scale from net p5 (-0.081); smaller than P1's because
# the keyframe net range is tighter. Reopen after any embedding provider/model
# swap (rule 3): rerun scripts/prewarm_and_measure_keyframes.py.
_KEYFRAME_BOOST_MAX = 0.05  # signed bonus cap when a frame clearly leans liked
_KEYFRAME_SUPPRESS_MAX = 0.04  # signed demotion cap when clearly leans disliked
_KEYFRAME_MARGIN = 0.05  # |s_pos - s_neg| at/above which the frames have an opinion
_KEYFRAME_CONTESTED = 0.45  # shares P1's contested threshold (same centroids)
# Margin to act in a contested region (2x normal), mirroring P1.
_KEYFRAME_CONTESTED_MARGIN = 0.10
# Net scale endpoints from the live keyframe measurement: p95=0.093, p5=-0.081.
_KEYFRAME_NET_P95 = 0.093
_KEYFRAME_NET_P5 = -0.081
_KEYFRAME_PENALTY_MAX = 0.05  # UNUSED (margin scoring supersedes, see _keyframe_bonus_from_vecs)
_KEYFRAME_DEFAULT_MAX_FRAMES = 4  # frames sampled per video


# Danmaku bonus (P2) — the only NON-visual member of this family. Bilibili
# candidates reach ranking with just title + description as semantics, and the
# description is frequently "求三连" boilerplate (body_text is empty on the
# Bilibili path). Condensed danmaku carry what the audience actually discusses.
#
# CALIBRATION PROVENANCE: PROVISIONAL / UNMEASURED. This is text↔text cosine
# against the same interest anchors the cover bonus uses, so it sits in a
# different numeric range from both the cross-modal cover↔text bonus and the
# same-modal image bonuses — hence its own floor/ceil rather than reusing
# either. Kept small + additive + one-directional. Reopen after running
# against a real embedding model: read the max_sim distribution, then move
# floor/ceil so the bonus spreads across the observed range (rule 3).
_DANMAKU_BONUS_MAX = 0.05  # hard cap on the additive nudge
_DANMAKU_SIM_FLOOR = 0.30  # text↔text cosine at/below → zero bonus
_DANMAKU_SIM_CEIL = 0.65  # text↔text cosine at/above → full bonus

# Sum of every per-signal bonus cap — the ceiling the stacked combined_bonus
# can reach before per-platform normalization. Normalization rescales each
# platform's combined_bonus to [0, this], so a platform missing signals
# (bangumi/xhs) still reaches the same height as Bilibili at its top.
# Visual signals are now SIGNED (margin scoring: boost +, suppress -), so the
# cap is the sum of the boost caps (the positive ceiling); signed normalization
# maps each platform's [g_min, g_max] -> [-cap, +cap].
_COMBINED_BONUS_CAP = (
    _VISUAL_COVER_BONUS_MAX
    + _VISUAL_PROFILE_BOOST_MAX
    + _KEYFRAME_BOOST_MAX
    + _DANMAKU_BONUS_MAX
)


@dataclass
class ExpressionBatchMalformed(Exception):  # noqa: N818 - specified domain interface
    """A successful provider response omitted or malformed batch members."""

    missing_items: tuple[DiscoveredContent, ...]
    completed: int = 0


@dataclass
class ExpressionCopyTransientError(Exception):
    """A retryable provider failure; coordinators decide when to retry."""

    kind: str
    completed: int = 0
    retry_after: float = 0.0


def _interests_by_weight(profile: SoulProfile) -> list[InterestTag]:
    """Interest tags sorted by weight (desc) so truncation keeps the strongest."""
    return sorted(profile.preferences.interests, key=lambda tag: tag.weight, reverse=True)


def _profile_style_summary(profile: SoulProfile) -> dict[str, object]:
    style = profile.preferences.style
    return {
        "preferred_duration": style.preferred_duration,
        "preferred_pace": style.preferred_pace,
        "humor_preference": style.humor_preference,
        "depth_preference": style.depth_preference,
    }


def _recommendation_profile_summary(
    profile: SoulProfile,
    *,
    interests: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Unified profile input for recommendation prompts.

    Delegates to :func:`build_profile_summary` so recommendation feeds the LLM
    the exact same structured profile as discovery: no ``personality_portrait``
    narrative, every other field included. Pass ``interests`` to substitute the
    embedding-selected, content-relevant tag list for the default weight-ranked
    one.
    """
    from openbiliclaw.discovery.strategies._utils import build_profile_summary

    return build_profile_summary(profile, interests=interests)


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


def _batch_results_by_content_key(
    payload: list[dict[str, Any]],
    batch: list[DiscoveredContent],
) -> dict[str, dict[str, Any]] | None:
    """Return payload entries keyed by content ID when the LLM supplied IDs.

    ``None`` means no usable IDs were present, so callers may fall back to
    legacy index matching only when the response length is complete.
    """
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


def _validated_expression_fields(
    result: object,
    *,
    content_key: str,
) -> tuple[str, str] | None:
    """Return (expression, topic_label) only when both are non-empty strings.

    The LLM occasionally answers with the whole batch nested under
    ``expression`` (``{"expression": [{...}, {...}], "topic_label": "..."}``).
    An unconditional ``str()`` turned that list into its Python repr, which
    passed the non-empty check and was persisted as card copy — users saw
    ``[{'expression': ..., 'topic_label': ...}]`` in the recommendation.
    Reject non-strings here instead, so the caller can fall back or retry.
    """
    if not isinstance(result, dict):
        return None
    expression = result.get("expression")
    topic_label = result.get("topic_label")
    if not isinstance(expression, str) or not isinstance(topic_label, str):
        logger.warning(
            "Discarding expression payload with non-string fields for %s "
            "(expression=%s, topic_label=%s)",
            content_key,
            type(expression).__name__,
            type(topic_label).__name__,
        )
        return None
    expression = expression.strip()
    topic_label = topic_label.strip()
    if not expression or not topic_label:
        logger.warning(
            "Discarding blank expression payload for %s "
            "(expression_empty=%s, topic_label_empty=%s)",
            content_key,
            not expression,
            not topic_label,
        )
        return None
    return (expression, topic_label)


class SupportsCoreMemoryTask(Protocol):
    """Protocol for a core-memory-aware structured LLM task executor."""

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
    ) -> LLMResponse: ...


class SupportsEmbeddingService(Protocol):
    """Embedding service protocol used by recommendation helpers."""

    similarity_threshold: float

    async def embed(self, text: str) -> list[float]: ...


@dataclass
class Recommendation:
    """A recommendation ready to present to the user."""

    content: DiscoveredContent
    recommendation_id: int = 0
    expression: str = ""  # Friend-style recommendation reason
    topic_label: str = ""  # Personal topic (not generic categories)
    confidence: float = 0.0  # How confident the agent is in this rec
    presented: bool = False
    feedback: str | None = None  # User feedback after seeing it


@dataclass(frozen=True)
class ServeTimings:
    """Phase timings for one recommendation serve request."""

    pool_snapshot_ms: float = 0.0
    embedding_ms: float = 0.0
    selector_worker_ms: float = 0.0
    event_loop_resume_delay_ms: float = 0.0
    persist_ms: float = 0.0


@dataclass(frozen=True)
class ServeResult:
    """Recommendation items plus post-commit inventory and timings."""

    items: list[Recommendation]
    pool_counts_after: dict[str, int]
    timings: ServeTimings = field(default_factory=ServeTimings)


@dataclass
class PersonalTopic:
    """A deeply personalized recommendation topic.

    Not generic labels like "Weekend Pack" but personal ones like:
    "你最近在探索摄影——这几个视频从你习惯的'搞明白原理'的角度讲构图"
    """

    title: str = ""
    description: str = ""
    recommendations: list[Recommendation] = field(default_factory=list)


class RecommendationEngine:
    """Produces warm, personalized recommendations.

    The engine takes discovered content and transforms it into
    friend-style recommendations with:
    - "我觉得" — subjective, personal judgment
    - "我理解你" — demonstrates deep understanding
    - Personal insights connecting content to the user's soul
    """

    def __init__(
        self,
        llm: SupportsCoreMemoryTask,
        database: Database,
        *,
        curator: PoolCurator | None = None,
        embedding_service: SupportsEmbeddingService | None = None,
        task_registry: BackgroundTaskRegistry | None = None,
        xhs_self_info_provider: Callable[[], dict[str, object] | None] | None = None,
        pool_inventory_commit_callback: Callable[..., object] | None = None,
        expression_batch_concurrency: int = _DEFAULT_EXPRESSION_BATCH_CONCURRENCY,
        visual_profile_enabled: bool = False,
        keyframe_enabled: bool = False,
        keyframe_max_frames: int = _KEYFRAME_DEFAULT_MAX_FRAMES,
        danmaku_enabled: bool = False,
        danmaku_max_chars: int = 500,
        bilibili_client: Any | None = None,
    ) -> None:
        self._llm = llm
        self._database = database
        self._curator = curator
        self._embedding_service = embedding_service
        self._visual_profile_enabled = bool(visual_profile_enabled)
        self._keyframe_enabled = bool(keyframe_enabled)
        self._keyframe_max_frames = max(1, min(12, int(keyframe_max_frames)))
        self._danmaku_enabled = bool(danmaku_enabled)
        self._danmaku_max_chars = max(100, min(2000, int(danmaku_max_chars)))
        # Optional Bilibili client for the danmaku prewarm. None = the danmaku
        # prewarm no-ops (the bonus path still works off already-stored text).
        self._bilibili_client = bilibili_client
        # In-memory cache of the user's visual-profile centroids (pos/neg),
        # rebuilt in the background by rebuild_visual_profile(). serve() reads
        # this only — never triggers a rebuild or a cover fetch on the hot path.
        # None = "not loaded yet / disabled"; an empty list = loaded but no
        # clusters (too little feedback). See _visual_profile_bonus_map.
        self._visual_profile_cache: list[dict[str, Any]] | None = None
        self._xhs_self_info_provider = xhs_self_info_provider
        self._pool_inventory_commit_callback = pool_inventory_commit_callback
        self._copy_pending_callback: Callable[[str], None] | None = None
        self._expression_batch_concurrency = max(1, min(16, int(expression_batch_concurrency)))
        # v0.3.63+: optional registry for detached fire-and-forget tasks
        # (classify_pool_backlog_detached, precompute_delight_scores_detached).
        # When provided, those tasks register here so RuntimeContext's
        # hot-reload can cancel them before the new runtime starts.
        # When None, the engine falls back to bare asyncio.create_task —
        # tests that don't inject a registry continue to work unchanged.
        self.task_registry: BackgroundTaskRegistry | None = task_registry
        self._classify_lock = asyncio.Lock()
        self._profile_prompt_caches: defaultdict[str, PromptLayerRenderCache] = defaultdict(
            PromptLayerRenderCache
        )
        # v0.3.47+: serialise precompute_pool_copy so multiple
        # per-strategy fire-and-forget tasks (now created from
        # _run_refresh_plan after each strategy completes) don't load
        # the same un-precomputed candidates and double-spend LLM tokens.
        #
        # v0.3.62+: split the previous single ``_precompute_lock`` into
        # two independent locks. The old shared lock serialised
        # expression generation and delight backfill — when the delight
        # backlog was large,
        # the next expression batch had to wait behind it even though
        # nothing about expression touches delight state. Now expression
        # generation holds ``_expression_lock`` while delight backfill
        # runs in a detached task guarded by ``_delight_lock``, so the
        # two flows progress independently and back-to-back precompute
        # calls still avoid duplicate delight writes.
        self._expression_lock = asyncio.Lock()
        self._delight_lock = asyncio.Lock()
        # Serialize the full snapshot→rank→commit lifecycle. Without this,
        # two simultaneous reshuffles can read the same fresh rows before
        # either short write commits and return duplicates.
        self._serve_lock = asyncio.Lock()
        # Background-computed supergroup canonical map. Populated by
        # prewarm_supergroup_embeddings() during refresh ticks; consumed
        # by serve()'s _merge_topic_supergroups for instant lookup.
        # Keys/values are normalised (stripped+lowered).
        self._supergroup_canonical_map: dict[str, str] = {}
        # v0.3.31+: track the previous served batch's bvids so the
        # debug-summary log can compute carryover (how many items in
        # the new batch were also in the previous batch). High
        # carryover signals stale-pool / fatigue-bypass.
        self._last_served_bvids: frozenset[str] = frozenset()

    def _profile_blocks(
        self,
        profile_summary: dict[str, object],
        *,
        cache_key: str,
    ) -> list[str]:
        """Render cached profile prompt layers for one recommendation task."""

        return self._profile_prompt_caches[cache_key].render_json_layers(
            profile_prompt_layers(profile_summary)
        )

    def _xhs_self_nickname(self) -> str:
        """Return the persisted XHS self nickname for pool guards."""
        if self._xhs_self_info_provider is None:
            return ""
        try:
            info = self._xhs_self_info_provider() or {}
        except Exception:
            logger.exception("Failed to load xhs self_info for pool guard")
            return ""
        if not isinstance(info, dict):
            return ""
        return str(info.get("nickname", "") or "").strip()

    def _pool_readiness_counts(self) -> dict[str, int]:
        nickname = self._xhs_self_nickname()
        readiness_fn = getattr(self._database, "count_pool_readiness", None)
        if callable(readiness_fn):
            try:
                counts = readiness_fn(xhs_self_nickname=nickname)
                available = int(counts.get("available", 0))
                return {
                    "available": max(0, available),
                    "raw": max(0, int(counts.get("raw", available))),
                    "pending": max(0, int(counts.get("pending", 0))),
                }
            except Exception:
                logger.exception("Failed to load pool readiness counts")
        available = int(self._database.count_pool_candidates(xhs_self_nickname=nickname))
        return {"available": max(0, available), "raw": max(0, available), "pending": 0}

    async def serve(
        self,
        profile: SoulProfile,
        *,
        limit: int = 5,
        excluded_bvids: frozenset[str] = frozenset(),
        expression_mode: Literal["realtime", "precomputed"] = "precomputed",
        source_platform: str = "",
    ) -> list[Recommendation]:
        """Serve recommendations while preserving the legacy list API."""
        result = await self.serve_with_result(
            profile,
            limit=limit,
            excluded_bvids=excluded_bvids,
            expression_mode=expression_mode,
            source_platform=source_platform,
        )
        return result.items

    async def serve_with_result(
        self,
        profile: SoulProfile,
        *,
        limit: int = 5,
        excluded_bvids: frozenset[str] = frozenset(),
        expression_mode: Literal["realtime", "precomputed"] = "precomputed",
        source_platform: str = "",
    ) -> ServeResult:
        """Serve one serialized batch with inventory/timing metadata."""
        async with self._serve_lock:
            return await self._serve_with_result_unlocked(
                profile,
                limit=limit,
                excluded_bvids=excluded_bvids,
                expression_mode=expression_mode,
                source_platform=source_platform,
            )

    def _enforce_platform_scope(
        self,
        candidates: list[DiscoveredContent],
        scope: str,
    ) -> list[DiscoveredContent]:
        """Drop candidates that do not belong to a requested platform scope.

        The candidate loaders are supposed to hand back a single family, so a
        mismatch here means the strict read drifted from its contract. Log it
        as an error and drop the rows: a scoped request must never leak
        another platform into the response, and letting the client filter it
        away would hide the drift instead of surfacing it.

        Classification must use ``source_family`` — the same function storage
        counts and selects with — not ``source_platform`` alone. Legacy rows
        carry their platform only in the ``source`` strategy prefix
        (``zhihu-hot``), and ``_rows_to_discovered`` defaults their blank
        ``source_platform`` to bilibili; judging by that column would discard
        real stock the tab is advertising and log a phantom contract violation
        on every scoped request.
        """
        if not scope:
            return candidates
        kept: list[DiscoveredContent] = []
        leaked: list[str] = []
        for item in candidates:
            family = source_family(item.source_strategy, item.source_platform)
            if family == scope:
                kept.append(item)
            else:
                leaked.append(f"{item.bvid}@{family}")
        if leaked:
            logger.error(
                "serve(source_platform=%s) candidate loader leaked %d cross-platform "
                "row(s): %s. Dropping them rather than returning off-platform picks.",
                scope,
                len(leaked),
                ", ".join(leaked[:10]),
            )
        return kept

    async def _serve_with_result_unlocked(
        self,
        profile: SoulProfile,
        *,
        limit: int,
        excluded_bvids: frozenset[str],
        expression_mode: Literal["realtime", "precomputed"],
        source_platform: str = "",
    ) -> ServeResult:
        """Unified recommendation entry point — always picks from the pool.

        All recommendation paths (generate, reshuffle, append) converge here.
        The engine is fully decoupled from Discovery: it only reads from the
        candidate pool in content_cache.

        Args:
            profile: User's soul profile for personalization.
            limit: Maximum number of recommendations.
            excluded_bvids: BVIDs already shown to the user (for pagination).
            expression_mode: ``"precomputed"`` uses pool-cached copy (fast),
                ``"realtime"`` generates fresh expressions via LLM (slow but
                higher quality).

        Returns:
            Recommendations plus inventory and phase timings.
        """
        label = "realtime" if expression_mode == "realtime" else "pool"
        scope = normalize_source_platform(source_platform)
        multiplier = 4 if excluded_bvids else 3
        candidate_limit = max(limit * multiplier, 40) + len(excluded_bvids)
        pool_snapshot_started = time.perf_counter()
        snapshot_loader = getattr(self._database, "load_pool_serve_snapshot_async", None)
        curator_snapshot: tuple[list[dict[str, object]], list[dict[str, object]]] | None = None
        if callable(snapshot_loader):
            history_limit = max(1, int(getattr(self._curator, "_history_window", 30)))
            # Only pass the new keyword when a scope was actually requested:
            # test doubles and third-party adapters implement the historical
            # signature, and a cross-platform serve must keep working on them.
            snapshot_kwargs: dict[str, Any] = {
                "limit": candidate_limit,
                "xhs_self_nickname": self._xhs_self_nickname(),
                "curator_history_limit": history_limit,
            }
            if scope:
                snapshot_kwargs["source_platform"] = scope
            snapshot = await snapshot_loader(**snapshot_kwargs)
            pool_readiness = dict(snapshot.readiness)
            candidates = self._enforce_platform_scope(
                self._rows_to_discovered(list(snapshot.candidate_rows)),
                scope,
            )
            loaded_count = int(snapshot.loaded_count)
            if snapshot.platform_topups:
                logger.info(
                    "serve platform floor topped up %s",
                    ", ".join(f"{name}+{count}" for name, count in snapshot.platform_topups),
                )
            if excluded_bvids:
                candidates = [item for item in candidates if item.bvid not in excluded_bvids]
            after_exclude_count = len(candidates)
            candidates = self._exclude_disliked_topic_candidates(candidates, profile)
            after_disliked_count = len(candidates)
            if snapshot.seen_bvids:
                candidates = [item for item in candidates if item.bvid not in snapshot.seen_bvids]
            after_viewed_count = len(candidates)
            curator_snapshot = (
                list(snapshot.curator_signals),
                list(snapshot.feedback_signals),
            )
        else:
            # Compatibility path for test doubles and third-party adapters.
            pool_readiness = await asyncio.to_thread(self._pool_readiness_counts)
            if int(pool_readiness.get("available", 0)) > 0:
                # Same rule as the snapshot loader: subclasses and test doubles
                # override this with the historical signature, so a
                # cross-platform serve must not hand them a new keyword.
                loader_kwargs: dict[str, Any] = {
                    "limit": candidate_limit,
                    "excluded_bvids": excluded_bvids,
                }
                if scope:
                    loader_kwargs["source_platform"] = scope
                (
                    candidates,
                    loaded_count,
                    after_exclude_count,
                    after_disliked_count,
                    after_viewed_count,
                ) = await asyncio.to_thread(
                    partial(self._load_filtered_serve_candidates, profile, **loader_kwargs)
                )
            else:
                candidates = []
                loaded_count = 0
                after_exclude_count = 0
                after_disliked_count = 0
                after_viewed_count = 0
        pool_snapshot_ms = (time.perf_counter() - pool_snapshot_started) * 1000.0
        servable_pool_count = pool_readiness["available"]
        raw_pool_count = pool_readiness["raw"]
        pending_pool_count = pool_readiness["pending"]
        if servable_pool_count <= 0:
            logger.info(
                "serve(/%s) skipped: no servable pool candidates (raw=%d pending=%d)",
                label,
                raw_pool_count,
                pending_pool_count,
            )
            self._last_served_bvids = frozenset()
            return ServeResult(
                items=[],
                pool_counts_after=pool_readiness,
                timings=ServeTimings(pool_snapshot_ms=pool_snapshot_ms),
            )
        if after_disliked_count < after_exclude_count:
            logger.info(
                "serve(/%s) filtered %d candidate(s) by profile disliked_topics",
                label,
                after_exclude_count - after_disliked_count,
            )
        if after_viewed_count == 0:
            logger.warning(
                "serve(/%s) loaded 0 usable candidates from servable=%d "
                "(raw=%d pending=%d) after filters: loaded=%d "
                "after_exclude=%d after_disliked=%d after_viewed=%d. Skipping curator, "
                "MMR embeddings, and recommendation writes.",
                label,
                servable_pool_count,
                raw_pool_count,
                pending_pool_count,
                loaded_count,
                after_exclude_count,
                after_disliked_count,
                after_viewed_count,
            )
            self._last_served_bvids = frozenset()
            return ServeResult(
                items=[],
                pool_counts_after=pool_readiness,
                timings=ServeTimings(pool_snapshot_ms=pool_snapshot_ms),
            )

        # Online supergroup merging — collapses semantically-equivalent
        # topic_groups within this batch (e.g. 动漫/动漫产业/动漫文化) so
        # the diversifier sees them as a single bucket. Adds 50–200ms of
        # embedding I/O to the hot path, traded for batch-level richness
        # that no offline precompute can guarantee at serve time.
        await self._merge_topic_supergroups(candidates)

        prev_bvids = self._last_served_bvids

        # Surface "pool says N but serve loads fewer" mismatches with enough
        # readiness detail to distinguish pending material from query drift.
        if servable_pool_count != loaded_count:
            logger.info(
                "serve(/%s) pool/load mismatch: count=%d → loaded=%d"
                " → after_exclude=%d → after_disliked=%d → after_viewed=%d "
                "(raw=%d pending=%d)",
                label,
                servable_pool_count,
                loaded_count,
                after_exclude_count,
                after_disliked_count,
                after_viewed_count,
                raw_pool_count,
                pending_pool_count,
            )

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Recommendation candidate summary (serve/%s): %s",
                label,
                json.dumps(
                    self._build_debug_summary(candidates, prev_bvids=prev_bvids),
                    ensure_ascii=False,
                ),
            )

        score_override, amplification_guard = await asyncio.to_thread(
            self._score_candidates_with_curator,
            candidates,
            curator_snapshot,
        )

        # v0.3.44+: pre-fetch embeddings for MMR-based diversification.
        # In v0.3.45+ discovery and classify_pool_backlog warm these into
        # the L2 SQLite cache up front, so this should be near-zero on
        # the hot path. The elapsed/coverage log below makes regressions
        # in cache warming visible — sustained "elapsed > 500ms" or
        # "coverage < 100%" means warm hooks are missing items.
        _embed_t0 = time.monotonic()
        embeddings = await self._fetch_candidate_embeddings(candidates)
        _embed_elapsed_ms = (time.monotonic() - _embed_t0) * 1000.0
        if candidates:
            logger.info(
                "MMR embedding fetch: coverage=%d/%d elapsed=%.0fms",
                len(embeddings),
                len(candidates),
                _embed_elapsed_ms,
            )

        # Cover-visual bonus (opt-in, multimodal embedding only): nudges the
        # ranking toward on-style covers, consistent with the delight surface.
        # Hot-path-safe — lookup-only (never fetches a cover on serve()); empty
        # map when multimodal is off, so the default feed ranking is unchanged.
        visual_bonus = await self._visual_bonus_map(candidates, profile)
        # User visual-profile bonus (P1, opt-in): cover vs the user's own
        # liked/disliked cover centroids. Independent of the cover↔text bonus
        # above and added on top; empty map when the feature is off or no
        # centroids are loaded, so the default feed ranking is unchanged.
        visual_profile_bonus = await self._visual_profile_bonus_map(candidates)
        # Video keyframe bonus (P3, opt-in): the user's visual centroids matched
        # against actual video frames rather than an UP-chosen cover. Third
        # independent signal, stacked the same way; empty map when off.
        keyframe_bonus = await self._keyframe_bonus_map(candidates)
        # Danmaku bonus (P2, opt-in): the only non-visual signal here — what
        # the audience discusses, which title+description miss entirely on the
        # Bilibili path. Empty map when off.
        danmaku_bonus = await self._danmaku_bonus_map(candidates, profile)
        combined_bonus: dict[str, float] = dict(visual_bonus)
        for extra in (visual_profile_bonus, keyframe_bonus, danmaku_bonus):
            for bvid, bonus in extra.items():
                combined_bonus[bvid] = combined_bonus.get(bvid, 0.0) + bonus

        # Cross-platform fairness: normalize the stacked bonus within each
        # platform's own pool to [0, _COMBINED_BONUS_CAP]. Without this, a
        # platform that lacks a signal (bangumi/xhs have no danmaku or
        # keyframes) is structurally shorter than Bilibili on combined_bonus
        # and gets squeezed out of the top — observed: enabling P2 dropped
        # bangumi in the top-25 from 3 to 1 purely on height. Per-platform
        # min-max normalization means a platform only loses *intra-platform*
        # discrimination for a missing signal, not *cross-platform* height:
        # the strongest bangumi candidate still reaches the same bonus height
        # as the strongest Bilibili candidate. No-op when combined_bonus is
        # empty (all signals off / no centroids) — ranking stays byte-identical.
        combined_bonus = self._normalize_bonus_per_platform(candidates, combined_bonus)

        (
            ranked,
            selector_worker_ms,
            event_loop_resume_delay_ms,
        ) = await self._select_diversified_batch_with_timing_async(
            candidates,
            limit=limit,
            score_override=score_override,
            embeddings=embeddings,
            amplification_guard=amplification_guard,
            relevance_bonus=combined_bonus,
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Recommendation picked summary (serve/%s): %s",
                label,
                json.dumps(
                    self._build_debug_summary(ranked, prev_bvids=prev_bvids),
                    ensure_ascii=False,
                ),
            )
        # Snapshot for the next call. Use bvid only — title might
        # legitimately repeat across different bvids and we want the
        # carryover signal to be at the canonical-id level.
        self._last_served_bvids = frozenset(item.bvid for item in ranked if item.bvid)

        recommendations: list[Recommendation] = []
        for item in ranked:
            rec = Recommendation(
                content=item,
                confidence=item.relevance_score,
                presented=False,
            )
            if expression_mode == "precomputed":
                rec.expression = item.pool_expression.strip()
                rec.topic_label = item.pool_topic_label.strip()
                # v0.3.57+: pool gate (get_pool_candidates SQL) now requires
                # pool_expression / pool_topic_label non-empty before a row
                # is considered in-pool, so this fallback path should never
                # fire in production. Keep it as a race-window safety net
                # and log loudly when it does — the warning is the canary.
                if not rec.expression:
                    logger.warning(
                        "Pool gate leak: bvid=%s pool_expression empty at "
                        "serve time (expected to be filtered out by "
                        "get_pool_candidates SQL). Falling back to template.",
                        item.bvid,
                    )
                    rec.expression = self._fallback_expression(item)
                if not rec.topic_label:
                    rec.topic_label = self._fallback_topic_label(profile)
            recommendations.append(rec)

        # Critical-path write: one short transaction on the dedicated serve
        # worker inserts history and marks the selected pool rows shown. This
        # removes the old cross-thread shared-connection access and closes the
        # duplicate window between insert and detached marking.
        recommendation_rows = [
            {
                "bvid": rec.content.bvid,
                "item_key": rec.content.item_key,
                "expression": rec.expression,
                "topic": rec.topic_label,
                "confidence": rec.confidence,
                "presented": 0,
            }
            for rec in recommendations
        ]
        ranked_bvids = [item.bvid for item in ranked]
        persist_started = time.perf_counter()
        isolated_persist = getattr(self._database, "persist_pool_serve_async", None)
        if callable(isolated_persist):
            persisted = await isolated_persist(recommendation_rows, ranked_bvids)
            ids = list(persisted.recommendation_ids)
            shown_committed = True
        else:
            ids = await asyncio.to_thread(
                self._database.batch_insert_recommendations,
                recommendation_rows,
            )
            shown_committed = False
        persist_ms = (time.perf_counter() - persist_started) * 1000.0
        for rec, rec_id in zip(recommendations, ids, strict=True):
            rec.recommendation_id = rec_id

        if expression_mode == "realtime":
            for rec, item in zip(recommendations, ranked, strict=True):
                rec.expression, rec.topic_label = await self.generate_expression(
                    item,
                    profile,
                )
                self._database.update_recommendation_content(
                    rec.recommendation_id,
                    expression=rec.expression,
                    topic=rec.topic_label,
                )

        consumed = len(ids)
        pool_counts_after = {key: max(0, int(value)) for key, value in pool_readiness.items()}
        for key in ("available", "raw"):
            if key in pool_counts_after:
                pool_counts_after[key] = max(0, pool_counts_after[key] - consumed)

        if shown_committed:
            self._schedule_pool_inventory_commit(pool_counts_after)
        else:
            # Compatibility path for adapters without isolated writes.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._mark_pool_shown_async(ranked_bvids))
            except RuntimeError:
                self._database.mark_pool_items_shown(ranked_bvids)
                await self._notify_pool_inventory_commit(pool_counts_after)
        return ServeResult(
            items=recommendations,
            pool_counts_after=pool_counts_after,
            timings=ServeTimings(
                pool_snapshot_ms=pool_snapshot_ms,
                embedding_ms=_embed_elapsed_ms,
                selector_worker_ms=selector_worker_ms,
                event_loop_resume_delay_ms=event_loop_resume_delay_ms,
                persist_ms=persist_ms,
            ),
        )

    async def _mark_pool_shown_async(self, bvids: list[str]) -> None:
        """Fire-and-forget pool-marking helper. Never raises."""
        try:
            # Keep this short UPDATE on the event-loop thread. Unlike the
            # awaited serve reads/writes above, this task intentionally
            # outlives ``serve()``; moving it to a worker lets callers close
            # the shared SQLite connection while the worker is still using
            # it (and can crash the interpreter inside sqlite3). The costly
            # mature-database scans remain off-loop, while this bounded write
            # normally updates only the served batch (10 rows).
            self._database.mark_pool_items_shown(bvids)
            await self._notify_pool_inventory_commit()
        except Exception:
            logger.exception(
                "mark_pool_items_shown (detached) failed for %d bvids",
                len(bvids),
            )

    def set_pool_inventory_commit_callback(self, callback: Callable[..., object] | None) -> None:
        """Set the hook run only after the shown-state write commits."""
        self._pool_inventory_commit_callback = callback

    def _schedule_pool_inventory_commit(self, counts: dict[str, int]) -> None:
        """Notify inventory observers after the response-critical DB commit."""
        coro = self._notify_pool_inventory_commit(counts)
        if self.task_registry is not None:
            self.task_registry.track("recommendation.pool_commit", coro)
            return
        asyncio.create_task(coro)

    async def _notify_pool_inventory_commit(
        self,
        counts: dict[str, int] | None = None,
    ) -> None:
        callback = self._pool_inventory_commit_callback
        if callback is None:
            return
        try:
            accepts_counts = False
            try:
                signature = inspect.signature(callback)
                accepts_counts = any(
                    parameter.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.VAR_POSITIONAL,
                    )
                    for parameter in signature.parameters.values()
                )
            except (TypeError, ValueError):
                pass
            result = callback(counts) if accepts_counts else callback()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("pool inventory commit callback failed")

    # Hybrid rule for online supergroup merging:
    #   - Strict embedding alone: sim >= 0.90 (catches 自走棋↔金铲铲之战
    #     0.902 across name boundaries).
    #   - Shared 2-char prefix + loose embedding: sim >= 0.80 (catches
    #     动漫族 0.80–0.88, 游戏族 0.84–0.87 — locality signal protects
    #     against transitive bridging that collapses a 40-group batch
    #     into one bucket).
    # Probe against live pool: should-merge band 0.80–0.92, should-
    # separate band caps near 0.82. Embedding alone at 0.83 cascades
    # via union-find; prefix gates loose-band merges.
    _SUPERGROUP_STRICT_THRESHOLD = 0.90
    _SUPERGROUP_LOOSE_THRESHOLD = 0.80
    _SUPERGROUP_PREFIX_LEN = 2

    @staticmethod
    def _build_supergroup_canonical_map(
        embeddings: dict[str, list[float]],
        *,
        strict: float,
        loose: float,
        prefix_len: int,
    ) -> dict[str, str]:
        """Build a deterministic union-find map from topic embeddings."""
        from openbiliclaw.llm.embedding import cosine_similarity

        labels = list(embeddings.keys())
        parent: dict[str, str] = {label: label for label in labels}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if rb < ra:
                ra, rb = rb, ra
            parent[rb] = ra

        for index, first in enumerate(labels):
            for second in labels[index + 1 :]:
                similarity = cosine_similarity(embeddings[first], embeddings[second])
                shared_prefix = (
                    first[:prefix_len] == second[:prefix_len] and len(first) >= prefix_len
                )
                if similarity >= strict or (shared_prefix and similarity >= loose):
                    union(first, second)

        return {label: canonical for label in labels if (canonical := find(label)) != label}

    @classmethod
    async def _build_supergroup_canonical_map_async(
        cls,
        embeddings: dict[str, list[float]],
        *,
        strict: float,
        loose: float,
        prefix_len: int,
    ) -> dict[str, str]:
        """Build the CPU-heavy pairwise map without blocking the event loop."""
        started = time.monotonic()
        result = await asyncio.to_thread(
            cls._build_supergroup_canonical_map,
            embeddings,
            strict=strict,
            loose=loose,
            prefix_len=prefix_len,
        )
        elapsed = time.monotonic() - started
        if elapsed > 0.05:
            logger.warning(
                "Topic supergroup CPU build took %.0fms in worker thread (%d labels)",
                elapsed * 1000.0,
                len(embeddings),
            )
        return result

    async def _merge_topic_supergroups(
        self,
        candidates: list[DiscoveredContent],
    ) -> None:
        """Apply the precomputed supergroup canonical map to candidates.

        The actual semantic merging happens in
        :meth:`prewarm_supergroup_embeddings`, which runs each refresh
        tick and uses ``"label | sample_titles"`` for accurate
        disambiguation of short Chinese labels (a label-only embedding
        of "赛博朋克" vs "动漫" can land at sim ≥ 0.90 and falsely
        collapse the entire entertainment family into one bucket).

        Serve-time is now a pure dict lookup — no embedding API calls,
        no pairwise comparison. When the map is empty (cold start, or
        the prewarmer hasn't run yet), this method is a no-op so we
        do not produce false-positive merges from on-the-fly label-only
        embeddings.
        """
        if not self._supergroup_canonical_map or len(candidates) < 2:
            return

        canonical_map = self._supergroup_canonical_map
        merges: list[tuple[str, str]] = []
        for item in candidates:
            key = (item.topic_group or "").strip().lower()
            if not key:
                continue
            canonical = canonical_map.get(key)
            if canonical and canonical != key:
                merges.append((key, canonical))
                item.topic_group = canonical

        if merges:
            # Dedup the log line — each (src, dst) pair shows once.
            unique_merges = sorted({m for m in merges})
            logger.info(
                "Topic supergroup merges (serve, cached): %s",
                ", ".join(f"{src}→{dst}" for src, dst in unique_merges),
            )

    async def _select_relevant_interests(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
        *,
        top_k: int = 5,
    ) -> list[dict[str, object]]:
        """Select interests most relevant to this content via embedding similarity.

        Falls back to top-K by weight when embedding service is unavailable.
        """
        # Candidate pool aligned with the profile summary's interest cap
        # (256): a niche interest outside the head ranks should still be
        # selectable when it's the best semantic match for this content.
        # top_k (5) still bounds how many actually reach the prompt, so the
        # wider pool improves coverage without growing prompt size.
        all_interests = [
            {"name": item.name, "category": item.category, "weight": item.weight}
            for item in _interests_by_weight(profile)[:256]
        ]
        if not all_interests:
            return []
        if self._embedding_service is None:
            return all_interests[:top_k]

        from openbiliclaw.llm.embedding import cosine_similarity

        content_text = f"{content.title} {content.description or ''}"
        content_vec = await self._embedding_service.embed(content_text)
        if not content_vec:
            return all_interests[:top_k]

        scored: list[tuple[dict[str, object], float]] = []
        for interest in all_interests:
            raw_weight = interest.get("weight", 0.0)
            weight = float(raw_weight) if isinstance(raw_weight, int | float | str) else 0.0
            interest_vec = await self._embedding_service.embed(str(interest["name"]))
            if not interest_vec:
                scored.append((interest, weight))
                continue
            sim = cosine_similarity(content_vec, interest_vec)
            # Blend embedding similarity with weight for ranking
            blended = sim * 0.7 + weight * 0.3
            scored.append((interest, blended))

        scored.sort(key=lambda x: -x[1])
        return [item for item, _ in scored[:top_k]]

    async def prewarm_supergroup_embeddings(self) -> int:
        """Compute the supergroup canonical map for use by the popup hot path.

        Embeds ``"{label} | {top-5 titles}"`` for every distinct
        ``topic_group`` in the fresh pool, then runs the union-find
        merge (strict 0.90, loose 0.80 with shared 2-char prefix) and
        stores the resulting ``label → canonical`` mapping in
        ``self._supergroup_canonical_map``. ``serve()`` then consumes
        this map as a pure dict lookup — no API calls, no pairwise
        comparison on the user's "换一批" click.

        Title context matters here: short Chinese labels are deceptively
        similar in raw embedding space (赛博朋克 ≈ 动漫 at sim ≥ 0.90
        without titles), and that bug looked like "30 of 40 candidates
        belong to one bucket" in production logs. The titles disambiguate.

        Returns the number of labels considered.
        """
        if self._embedding_service is None:
            self._supergroup_canonical_map = {}
            return 0

        groups = self._database.get_topic_group_samples()
        logger.info(
            "Topic supergroup prewarm: %d groups (top-by-population)",
            len(groups),
        )
        if len(groups) < 2:
            self._supergroup_canonical_map = {}
            return len(groups)

        embedding_service = self._embedding_service

        async def _embed_with_titles(label: str, titles: list[str]) -> tuple[str, list[float]]:
            text = f"{label} | {' | '.join(titles)}" if titles else label
            vec = await embedding_service.embed(text)
            return label.lower(), vec

        results = await asyncio.gather(
            *(_embed_with_titles(label, titles) for label, titles in groups)
        )
        embeddings: dict[str, list[float]] = {label: vec for label, vec in results if vec}
        if len(embeddings) < 2:
            self._supergroup_canonical_map = {}
            return len(embeddings)

        labels = list(embeddings.keys())
        new_map = await self._build_supergroup_canonical_map_async(
            embeddings,
            strict=self._SUPERGROUP_STRICT_THRESHOLD,
            loose=self._SUPERGROUP_LOOSE_THRESHOLD,
            prefix_len=self._SUPERGROUP_PREFIX_LEN,
        )
        self._supergroup_canonical_map = new_map

        if new_map:
            logger.info(
                "Topic supergroup canonical map rebuilt (prewarm): %d labels, %d merges",
                len(labels),
                len(new_map),
            )
            # v0.3.56+: also update existing pool rows to the canonical
            # form. Without this, ``Recommendation candidate summary``
            # logs show "动漫" / "动漫杂谈" / "动漫二次元" as 3 separate
            # topic_groups even after the map says they're synonyms,
            # because the merge only ran at serve time. Mass-update
            # makes downstream SQL (`get_topic_group_samples`,
            # `count_pool_by_franchise`-equivalent group-by analytics,
            # popup status displays) see the same canonical form
            # serve-time would.
            canonicalize = getattr(self._database, "canonicalize_topic_groups", None)
            if callable(canonicalize):
                try:
                    rewritten = canonicalize(new_map)
                    if rewritten:
                        logger.info(
                            "Topic supergroup canonical map applied to pool: %d row(s) rewritten",
                            rewritten,
                        )
                except Exception:
                    logger.exception(
                        "canonicalize_topic_groups failed; pool topic_group "
                        "values will lazy-merge at serve time only"
                    )
        return len(labels)

    async def prewarm_pool_mmr_embeddings(self, *, limit: int = 200) -> int:
        """Warm the MMR embedding L2 cache for the current pool.

        Companion to ``warm_mmr_embeddings`` (which fires per-item at
        discovery / classification time) — this method handles the
        migration / cold-restart case where the pool already contains
        items that pre-date the warming hooks. Called from the refresh
        loop and at startup so the next ``serve()`` is an L2 hit even
        on day 1 of a deploy.

        ``limit`` defaults to 200 — covers the candidate window that
        ``serve()`` actually pulls from, sized so a fresh-restart warm
        completes in a few minutes against a slow local embedding
        provider (Ollama). Idempotent: ``EmbeddingService.embed``
        short-circuits on L2 hit.

        Return contract (lever 4 observability — let callers tell a benign
        cold start from a broken embedding backend):
          * ``>0`` — items warmed.
          * ``0``  — there WERE candidates but none embedded → the embedding
            backend is unreachable (e.g. Ollama down). Worth retrying.
          * ``-1`` — nothing to warm (no embedding service, or pool empty);
            retrying is pointless — the cache lazy-fills as the pool fills.
        """
        if self._embedding_service is None:
            logger.debug("Pool MMR prewarm skipped: embedding service not configured")
            return -1
        candidates = self._load_pool_candidates(limit=limit)
        if not candidates:
            logger.debug(
                "Pool MMR prewarm skipped: pool has no servable candidates yet — "
                "nothing to warm (cache lazy-fills as discovery classifies the pool)"
            )
            return -1
        warmed = await self.warm_mmr_embeddings(candidates)
        if warmed == 0:
            logger.warning(
                "Pool MMR prewarm: 0/%d items embedded — the embedding backend "
                "looks unreachable (e.g. Ollama down). Recommendation diversity "
                "(MMR) degrades until it recovers; see embed-failure debug logs.",
                len(candidates),
            )
        else:
            logger.info(
                "Pool MMR embedding prewarm: %d/%d items warmed",
                warmed,
                len(candidates),
            )
        # Backfill cover embeddings for the same pool window when multimodal is
        # active — so enabling it later doesn't leave older pool content without
        # a cover vector while newly-discovered items get warmed at admission.
        # No-op (and zero cost) when multimodal is off. Best-effort: a cover
        # failure must not affect the MMR prewarm return contract above.
        if self._cover_embedding_active():
            try:
                await self._prewarm_pool_covers(candidates)
            except Exception:
                logger.debug("Pool cover prewarm raised (ignored)", exc_info=True)
        # P3: fetch + embed video keyframes for pool rows that lack them. Same
        # best-effort contract as the cover prewarm — it must never affect the
        # MMR return value above. No-op when the flag or multimodal is off.
        if self._keyframe_active():
            try:
                await self.prewarm_pool_keyframes()
            except Exception:
                logger.debug("Pool keyframe prewarm raised (ignored)", exc_info=True)
        # P2: fetch + condense + embed danmaku for pool rows that lack them.
        # Same best-effort contract; no-op when the flag is off or no Bilibili
        # client was injected.
        if self._danmaku_active():
            try:
                await self.prewarm_pool_danmaku()
            except Exception:
                logger.debug("Pool danmaku prewarm raised (ignored)", exc_info=True)
        return warmed

    async def precompute_pool_copy(
        self,
        *,
        profile: SoulProfile,
        limit: int = 20,
        delight_limit: int = 30,
        batch_size: int = _DEFAULT_EXPRESSION_BATCH_SIZE,
    ) -> int:
        """Precompute fast-path popup copy for fresh pool candidates.

        v0.3.47+: batches dispatched in parallel via ``asyncio.gather``,
        bounded by ``expression_batch_concurrency`` (default 2), and
        ``batch_size`` defaults to 30. Real-provider concurrency testing
        showed 45 can occasionally produce malformed batch JSON on
        recommendation copy, so this path stays conservative while
        discovery eval uses the larger text batch.
        With the previous serial × ``batch_size=8`` shape, a 60-item
        backlog needed 8 LLM calls and 8 sequential round trips. The new
        shape needs 2 LLM calls running concurrently — popup copy
        catches up minutes faster.

        v0.3.62+: expression generation is guarded by
        ``self._expression_lock``; delight backfill runs in a detached
        ``asyncio.create_task`` with its own ``self._delight_lock``. The
        previous single ``_precompute_lock`` held both flows under one
        gate, so a slow delight pass would stall the next expression
        batch even though pool items already needed ``pool_expression``.
        Splitting the locks lets expression and delight progress
        independently while the per-flow lock still prevents
        back-to-back fires from updating the same delight rows twice.

        The per-strategy fire-and-forget tasks queued from
        ``_run_refresh_plan`` therefore can't load the same
        un-precomputed candidates twice for expression generation.

        Also backfills delight fields from Evo's relevance result for
        un-scored candidates, including card reasons for items above the
        delight threshold.

        Args:
            profile: Current soul profile used for personalisation.
            limit: Max pool candidates to generate expression copy for.
            delight_limit: Max un-scored candidates to evaluate for delight
                potential. Independent from ``limit`` because delight scans
                the whole pool for missing scores, not just items that need
                expression copy — sharing one limit would starve delight
                scoring whenever the copy queue is short.
            batch_size: Batch size for expression generation LLM calls.
        """
        # v0.3.59+: classify_pool_backlog fires as a detached task instead
        # of awaiting. Previously precompute waited for classify to finish
        # before reading candidates — under v_voucher rate limit this
        # serialised the entire pipeline because classify backlog could
        # take minutes per cycle. Production logs (2026-05-05 21:15-21:36)
        # showed pool_available stuck at 0 for 16+ min because precompute
        # was queued behind classify. Now both run on their own cadence;
        # precompute reads whatever's available right now and the periodic
        # refresh-loop drain (runtime/refresh.py:_drain_pool_precompute_backlog)
        # picks up freshly-classified items on the next tick.
        try:
            self._spawn_detached_task(
                "classify_pool_backlog_detached",
                self._safe_classify_pool_backlog(profile=profile, limit=limit),
            )
        except Exception:
            logger.exception("classify_pool_backlog detach failed, continuing with precompute")

        # v0.3.62+: delight scoring runs detached so it doesn't block
        # expression generation or the caller. Its own _delight_lock
        # (taken inside _safe_precompute_delight_scores) keeps
        # back-to-back fires from re-scoring the same items.
        def _spawn_delight() -> None:
            try:
                self._spawn_detached_task(
                    "precompute_delight_scores_detached",
                    self._safe_precompute_delight_scores(
                        profile=profile,
                        limit=delight_limit,
                    ),
                )
            except Exception:
                logger.exception("precompute_delight_scores detach failed")

        completed = await self._drain_expression_copy(
            profile=profile, limit=limit, batch_size=batch_size
        )

        # Fire delight scoring outside the expression lock so the next
        # expression batch can start immediately while delight catches up.
        _spawn_delight()
        return completed

    async def _drain_expression_copy(
        self,
        *,
        profile: SoulProfile,
        limit: int,
        batch_size: int = _DEFAULT_EXPRESSION_BATCH_SIZE,
        max_extra_requests: int = 6,
    ) -> int:
        """Generate popup copy for classified-but-uncopied pool candidates.

        Copy-only: unlike :meth:`precompute_pool_copy` it does NOT spawn
        classify / delight, so the post-classify hook
        (:meth:`_safe_classify_pool_backlog`, lever 2b) can call it the
        moment freshly-classified items become copy-eligible — draining
        their expression copy in the same cycle instead of waiting for the
        next refresh-loop tick — without re-entering classify. The shared
        ``_expression_lock`` serialises it against the regular precompute
        pass so the same items are never double-spent on LLM tokens.
        """
        async with self._expression_lock:
            candidates = self._load_pool_candidates_needing_copy(limit=max(0, limit))
            if not candidates:
                return 0

            batches = [
                candidates[i : i + batch_size] for i in range(0, len(candidates), batch_size)
            ]
            results: list[int | Exception | None] = [None] * len(batches)
            next_batch_index = 0
            worker_count = min(self._expression_batch_concurrency, len(batches))

            async def _worker() -> None:
                nonlocal next_batch_index
                while next_batch_index < len(batches):
                    batch_index = next_batch_index
                    next_batch_index += 1
                    try:
                        results[batch_index] = await self._precompute_batch_with_split_retry(
                            batches[batch_index],
                            profile,
                            max_extra_requests=max_extra_requests,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        results[batch_index] = exc

            await asyncio.gather(*(_worker() for _ in range(worker_count)))
            completed = 0
            for r in results:
                if isinstance(r, Exception):
                    continue
                completed += int(r or 0)
            critical: Exception | None = None
            unavailable: Exception | None = None
            transient_kind: str | None = None
            retry_after = 0.0
            for result in results:
                if not isinstance(result, Exception):
                    continue
                completed += int(getattr(result, "completed", 0) or 0)
                kind = getattr(result, "kind", None) or classify_llm_failure_kind(result)
                if kind in {"rate_limited", "timeout", "connection", "server_error"}:
                    transient_kind = str(kind)
                    retry_after = max(
                        retry_after,
                        self._retry_after_seconds(result),
                        float(getattr(result, "retry_after", 0.0) or 0.0),
                    )
                    critical = result
                elif kind in {"auth_failed", "no_provider"} and unavailable is None:
                    unavailable = result
                else:
                    logger.warning("Expression batch failed: %s", result)
            if unavailable is not None:
                raise unavailable
            if transient_kind is not None:
                raise ExpressionCopyTransientError(
                    kind=transient_kind,
                    completed=completed,
                    retry_after=retry_after,
                ) from critical
            if critical is not None:
                raise critical
        return completed

    @staticmethod
    def _retry_after_seconds(exc: BaseException) -> float:
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            value = getattr(current, "retry_after", None)
            if isinstance(value, int | float) and value > 0:
                return float(value)
            current = current.__cause__ or current.__context__
        return 0.0

    async def drain_pending_expression_copy(
        self,
        *,
        profile: SoulProfile,
        limit: int = 60,
        max_extra_requests: int = 6,
    ) -> int:
        """Drain only durable classified rows awaiting expression copy.

        ``max_extra_requests`` controls split retries after a provider returns
        a malformed or partial batch.  The default preserves the daemon/API
        repair behavior; short-lived callers may set it to zero to persist a
        valid subset and leave the remaining rows durable for a later pass.
        """

        return await self._drain_expression_copy(
            profile=profile,
            limit=max(0, min(60, int(limit))),
            batch_size=_DEFAULT_EXPRESSION_BATCH_SIZE,
            max_extra_requests=max(0, int(max_extra_requests)),
        )

    def set_copy_pending_callback(self, callback: Callable[[str], None] | None) -> None:
        """Register the runtime generation's non-blocking copy notifier."""

        self._copy_pending_callback = callback

    # ── Source-agnostic content classification ───────────────────────
    #
    # Content from any source (bilibili, xiaohongshu, web, …) must carry
    # the same set of content features (style_key, topic_group,
    # relevance_score) before it enters the diversity/ranking pipeline.
    # Items that lack these features would collapse _select_diversified_batch
    # — all sharing "unknown" style and a single fallback topic token.
    #
    # classify_pool_backlog() is now a legacy/recovery gate: it picks up
    # old rows that are already in content_cache without content features
    # (for example, rows inserted before the discovery_candidates staging
    # table existed), runs them through the same LLM evaluation used for
    # discovery, and writes results back.  Normal source ingest should enter
    # discovery_candidates first and be evaluated before content_cache.

    def _spawn_detached_task(
        self,
        name: str,
        coro: Coroutine[Any, Any, Any],
    ) -> asyncio.Task[Any]:
        """Spawn a detached task, routing through the registry when available.

        v0.3.63+: when ``self.task_registry`` is wired (by
        ``RuntimeContext`` at startup), the task is registered so that
        ``rebuild_from_config``'s ``cancel_all`` can cancel it before
        the new runtime starts. Tests that construct
        ``RecommendationEngine`` directly (no registry) fall back to
        bare ``asyncio.create_task`` for backward compat.
        """
        registry = self.task_registry
        if registry is not None:
            return registry.track(name, coro)
        return asyncio.create_task(coro, name=name)

    async def _safe_classify_pool_backlog(
        self,
        *,
        profile: SoulProfile,
        limit: int = 30,
    ) -> int:
        """Detached-task wrapper for classify_pool_backlog (v0.3.59+).

        ``precompute_pool_copy`` schedules this as ``asyncio.create_task``
        instead of ``await``-ing classify_pool_backlog directly. The
        previous serial coupling let a slow classify (under v_voucher
        backoff or a flood of fresh XHS notes) stall precompute for
        minutes; now precompute reads whatever's classified-ready right
        now while classify catches up in parallel.

        v0.3.124+ (lever 2b): when classify actually labels new items, drain
        their expression copy immediately rather than leaving them for the
        next refresh-loop precompute tick. This closes the "classified but
        not yet serveable" gap (the items still need ``pool_expression`` /
        ``pool_topic_label`` before the pool-availability gate counts them).
        The drain is copy-only so it can't re-enter classify, and the
        shared ``_expression_lock`` serialises it against the in-flight
        precompute pass.
        """
        try:
            classified = await self.classify_pool_backlog(profile=profile, limit=limit)
        except Exception:
            logger.exception("classify_pool_backlog (detached) failed")
            return 0
        if classified > 0:
            if self._copy_pending_callback is not None:
                try:
                    self._copy_pending_callback(f"classified:{classified}")
                except Exception:
                    logger.warning("post-classify expression notification failed", exc_info=True)
            else:
                await self.drain_pending_expression_copy(
                    profile=profile, limit=max(limit, classified)
                )
        return classified

    async def _safe_precompute_delight_scores(
        self,
        *,
        profile: SoulProfile,
        limit: int,
    ) -> int:
        """Detached-task wrapper for precompute_delight_scores (v0.3.62+).

        ``precompute_pool_copy`` schedules this as ``asyncio.create_task``
        instead of awaiting it inline. The previous shared
        ``_precompute_lock`` made delight backfill stall the next
        expression batch whenever the delight queue was large —
        pool items would sit waiting for ``pool_expression`` even
        though expression generation itself was idle. Splitting the
        work into a detached task with its own ``_delight_lock`` keeps
        delight from blocking expression while still preventing two
        precompute fires from re-scoring the same items.
        """
        if self._delight_lock.locked():
            return 0
        async with self._delight_lock:
            try:
                return await self.precompute_delight_scores(profile=profile, limit=limit)
            except Exception:
                logger.exception("precompute_delight_scores (detached) failed")
                return 0

    async def classify_pool_backlog(
        self,
        *,
        profile: SoulProfile,
        limit: int = 30,
        batch_size: int = 10,
    ) -> int:
        """Legacy/recovery path for cached rows lacking style / topic / score.

        Normal source ingest now writes ``discovery_candidates`` and uses the
        shared discovery-candidate pipeline before rows enter ``content_cache``.
        This method remains as a safety net for legacy databases and recovery
        jobs where rows are already cached but still missing ``style_key``,
        ``topic_group``, or ``relevance_score``.

        Returns:
            Number of items classified.
        """
        if self._classify_lock.locked():
            return 0  # Another classify task is already running
        async with self._classify_lock:
            return await self._classify_pool_backlog_locked(
                profile=profile,
                limit=limit,
                batch_size=batch_size,
            )

    async def _classify_pool_backlog_locked(
        self,
        *,
        profile: SoulProfile,
        limit: int,
        batch_size: int,
    ) -> int:
        """Inner implementation of classify_pool_backlog, called under lock."""
        rows = self._database.get_pool_candidates_needing_evaluation(
            limit=limit, xhs_self_nickname=self._xhs_self_nickname()
        )
        if not rows:
            return 0

        items = self._rows_to_discovered(rows)
        logger.info(
            "classify_pool_backlog: %d un-classified items (platforms: %s)",
            len(items),
            ", ".join(sorted({item.source_platform or "unknown" for item in items})),
        )

        classified = 0
        for batch_start in range(0, len(items), batch_size):
            batch = items[batch_start : batch_start + batch_size]
            try:
                await self._classify_batch(batch, profile)
            except Exception:
                logger.exception(
                    "classify_pool_backlog: batch failed (%d items)",
                    len(batch),
                )
                continue

            # Persist results back to the pool.
            persisted: list[DiscoveredContent] = []
            for item in batch:
                # Use topic_group as topic_key when the original is empty —
                # diversity tokens fall back to topic_key, so this is critical.
                if not item.topic_key and item.topic_group:
                    item.topic_key = item.topic_group
                try:
                    self._database.cache_content(
                        content_storage_key(
                            item.source_platform,
                            item.content_id or item.bvid,
                            item.content_url,
                        ),
                        **item.to_cache_kwargs(),
                    )
                    classified += 1
                    persisted.append(item)
                except Exception:
                    logger.exception(
                        "classify_pool_backlog: failed to persist %s",
                        item.bvid,
                    )

            # Pre-warm the MMR embedding cache so the next reshuffle is an
            # L2 hit instead of paying ~150ms × N for serial API calls in
            # serve(). Best-effort — failures fall back to the
            # string-cap-only path at serve time.
            if persisted:
                await self.warm_mmr_embeddings(persisted)

        logger.info(
            "classify_pool_backlog: %d/%d items classified (styles: %s, topics: %s)",
            classified,
            len(items),
            ", ".join(sorted({i.style_key or "unknown" for i in items})),
            ", ".join(sorted({i.topic_group or "unknown" for i in items})),
        )
        return classified

    async def _classify_batch(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
    ) -> None:
        """Run batched LLM evaluation on a group of un-classified items.

        Mutates each item in-place: sets ``relevance_score``,
        ``relevance_reason``, ``topic_group``, and ``style_key``.
        """
        from openbiliclaw.llm.prompts import build_batch_content_evaluation_prompt

        profile_data = _recommendation_profile_summary(profile)
        content_items = [
            {
                "bvid": c.bvid,
                "content_id": c.content_id or c.bvid,
                "title": c.title,
                "up_name": c.up_name or c.author_name,
                "description": (c.description or "")[:400],
                "duration": c.duration,
                "view_count": c.view_count,
                "source_strategy": c.source_strategy,
                "content_type": c.content_type,
                # Text-first items (X tweets/threads) carry their full text
                # here — titles are low-information for those, so the LLM
                # needs body_text to judge relevance. Empty for video sources.
                "body_text": c.body_text,
            }
            for c in batch
        ]
        # Fetch recent negative exemplars so Rule 11 pattern-matching
        # applies equally to non-bilibili pool items (e.g. xiaohongshu).
        negative_examples: list[dict[str, object]] | None = None
        try:
            from openbiliclaw.soul.negative_exemplars import recent_negative_exemplars

            negative_examples = recent_negative_exemplars(self._database) or None
        except Exception:
            logger.debug("classify_batch: negative_exemplars unavailable", exc_info=True)

        # Determine the dominant platform for prompt context
        platform = (batch[0].source_platform or "bilibili") if batch else "bilibili"
        messages = build_batch_content_evaluation_prompt(
            profile_summary=profile_data,
            profile_blocks=self._profile_blocks(profile_data, cache_key="evaluate_batch"),
            content_items=content_items,
            source_context=batch[0].source_strategy if batch else "",
            source_platform=platform,
            negative_examples=negative_examples,
        )

        complete_structured = self._llm.complete_structured_task
        response = await complete_structured(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            max_tokens=8192,
            # v0.3.51+: structured XHS classification — pure score +
            # categorical fields, doesn't benefit from reasoning chain.
            reasoning_effort="",
            caller="recommendation.evaluate_batch",
            **without_core_memory_kwargs(complete_structured),
        )
        raw = str(getattr(response, "content", "")).strip()
        payload = extract_llm_json_list(
            raw,
            wrapper_keys=("results", "items", "evaluations", "scores", "data"),
            allow_singleton=True,
            item_predicate=lambda item: "score" in item,
        )
        if payload is None:
            raise ValueError("Expected classification JSON array or compatible wrapper.")

        if len(payload) != len(batch):
            logger.warning(
                "LLM returned %d results for %d items in classification batch",
                len(payload),
                len(batch),
            )

        payload_by_id = _batch_results_by_content_key(payload, batch)
        if payload_by_id is None and len(payload) != len(batch):
            logger.warning(
                "Classification batch result count mismatch without IDs; marking %d items failed",
                len(batch),
            )
            for content in batch:
                content.relevance_score = 0.01
                content.relevance_reason = "classification_failed"
            return

        for i, content in enumerate(batch):
            if payload_by_id is None:
                result = payload[i] if i < len(payload) else None
            else:
                result = next(
                    (
                        payload_by_id[key]
                        for key in _content_result_keys(content)
                        if key in payload_by_id
                    ),
                    None,
                )
            if not isinstance(result, dict):
                # Mark as attempted so get_pool_candidates_needing_evaluation
                # won't retry this item forever.  A score of 0.01 signals
                # "classification attempted but no usable result".
                content.relevance_score = 0.01
                content.relevance_reason = "classification_failed"
                continue
            score_value = result.get("score", 0.0)
            if not isinstance(score_value, (int, float, str)):
                score_value = 0.0
            score = max(0.0, min(1.0, float(score_value)))
            reason = validated_text_field(
                result.get("reason", ""), field="reason", content_key=content.bvid
            )
            topic_group = validated_text_field(
                result.get("topic_group", ""), field="topic_group", content_key=content.bvid
            )
            if reason is None or topic_group is None:
                # Keep malformed evaluator output out of persisted diagnostics;
                # delight display copy is gated separately on pool_expression.
                content.relevance_score = 0.01
                content.relevance_reason = "classification_failed"
                continue
            style_key = normalize_style_key(result.get("style_key", ""))

            content.relevance_score = score or 0.01  # never leave at 0.0
            content.relevance_reason = reason
            if topic_group:
                content.topic_group = topic_group
            if style_key in VALID_STYLE_KEYS:
                content.style_key = style_key

    async def precompute_delight_scores(
        self,
        *,
        profile: SoulProfile,
        limit: int = 50,
    ) -> int:
        """Populate proactive delight fields from Evo relevance output.

        Evo already runs the expensive candidate evaluator and the pool-copy
        path writes user-facing ``pool_expression`` into ``content_cache``.
        Delight starts only after both ``pool_expression`` and
        ``pool_topic_label`` are ready, then reuses ``relevance_score`` and
        atomically snapshots that formal copy. The evaluator's diagnostic
        ``relevance_reason`` is never delight state or display copy.
        """
        from openbiliclaw.recommendation.delight import effective_delight_threshold

        prefs = getattr(profile, "preferences", None)
        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))
        default_threshold = effective_delight_threshold(exploration_openness)
        dynamic_threshold = getattr(self._database, "dynamic_delight_threshold", None)
        effective_threshold = (
            float(dynamic_threshold(default_threshold=default_threshold))
            if callable(dynamic_threshold)
            else default_threshold
        )
        rows = self._database.get_pool_candidates_needing_delight_score(
            limit=limit,
            min_delight_score_for_reason=effective_threshold,
            xhs_self_nickname=self._xhs_self_nickname(),
        )
        if not rows:
            return 0

        candidates = self._rows_to_discovered(rows)

        # Cover-visual alignment is opt-in (multimodal embedding). Embed the
        # profile interest anchors ONCE per run — they're identical across all
        # candidates — so the per-candidate cost is just a cached image lookup
        # plus cosines. Empty list => bonus stays 0 for every candidate (the
        # text-only default path is byte-identical to before).
        visual_anchor_vecs = await self._visual_anchor_vectors(profile)

        scored_count = 0
        for candidate in candidates:
            persisted_score = max(0.01, min(1.0, float(candidate.relevance_score or 0.0)))
            if persisted_score < effective_threshold:
                persisted = self._database.update_delight_score(
                    candidate.bvid,
                    delight_score=persisted_score,
                    delight_reason="",
                    delight_hook="",
                )
                if persisted:
                    scored_count += 1
                continue

            reason = self._evo_delight_reason(candidate)
            hook = self._evo_delight_hook(candidate)
            if not reason or not hook:
                # The storage query must never return an uncopied row. Keep an
                # engine-level guard for adapters and read/write races so no
                # delight score is written before formal copy is ready.
                logger.warning(
                    "Delight readiness gate leak: bvid=%s has incomplete formal copy",
                    candidate.bvid,
                )
                continue

            # Only nudge candidates that ALREADY qualify: visual never changes
            # who becomes a delight candidate, only their ranking score among
            # qualifiers, and it can only add (never demote).
            visual_bonus = await self._visual_cover_bonus(candidate, visual_anchor_vecs)
            final_score = min(1.0, persisted_score + visual_bonus)

            persisted = self._database.update_delight_score(
                candidate.bvid,
                delight_score=final_score,
                delight_reason=reason,
                delight_hook=hook,
            )
            if not persisted:
                logger.info(
                    "Delight admission deferred because formal copy changed: %s",
                    candidate.bvid,
                )
                continue
            scored_count += 1
            logger.info(
                "Delight candidate found from Evo result: %s (score=%.3f, "
                "visual_bonus=%.3f, hook=%s)",
                candidate.bvid,
                final_score,
                visual_bonus,
                hook,
            )

        # P1: opportunistically rebuild the user visual profile in the same
        # background tick. Throttled — only when feedback is newer than the
        # last centroid rebuild — so an idle feedback state doesn't re-embed
        # covers every cycle. Best-effort; never raises into the caller.
        if self._visual_profile_active():
            try:
                self._maybe_rebuild_visual_profile()
            except Exception:
                logger.debug("visual profile rebuild dispatch failed", exc_info=True)

        return scored_count

    def _maybe_rebuild_visual_profile(self) -> None:
        """Dispatch a throttled visual-profile rebuild if feedback is fresh.

        Compares the newest ``recommendations.feedback_at`` against the newest
        ``user_visual_clusters.updated_at``; only rebuilds when feedback is
        newer (or no centroids exist yet). Fire-and-forget on the task registry
        when present, else a bare create_task — same pattern as the delight
        detached tasks.
        """
        try:
            latest_feedback = self._database.latest_feedback_at()
        except Exception:
            logger.debug("latest_feedback_at query failed", exc_info=True)
            return
        if not latest_feedback:
            return
        try:
            existing = self._database.get_user_visual_clusters()
        except Exception:
            logger.debug("get_user_visual_clusters query failed", exc_info=True)
            return
        if existing:
            latest_rebuild = max(
                (str(r.get("updated_at") or "") for r in existing),
                default="",
            )
            if latest_rebuild and latest_feedback <= latest_rebuild:
                return  # feedback not newer than the last rebuild

        async def _run() -> None:
            try:
                await self.rebuild_visual_profile()
            except Exception:
                logger.debug("rebuild_visual_profile failed", exc_info=True)

        # BackgroundTaskRegistry's method is ``track`` (runtime/task_registry.py),
        # not ``create_task``. Probing for the wrong name silently fell through
        # to a bare create_task, leaving the rebuild untracked and surviving
        # hot reload — the registry exists precisely so RuntimeContext can
        # cancel detached work before swapping runtimes.
        registry = self.task_registry
        if registry is not None and hasattr(registry, "track"):
            try:
                registry.track("rebuild_visual_profile_detached", _run())
                return
            except Exception:
                logger.debug("task_registry dispatch failed; falling back", exc_info=True)
        loop = asyncio.get_event_loop()
        loop.create_task(_run())

    async def _visual_anchor_vectors(self, profile: SoulProfile) -> list[list[float]]:
        """Embed the profile's top interest names once for cover alignment.

        Returns ``[]`` (disabling the visual bonus) when image embedding is
        inactive, there's no embedding service, or the profile has no usable
        interest anchors — so callers can stay on the zero-cost text-only path.

        Hot-path contract: anchor vectors are resolved cache-first via
        ``lookup_cached`` (L1 in-memory → L2 SQLite, never a provider API
        call). Only on a genuine cold miss — a brand-new interest name never
        embedded in this deployment, or its L2 key evicted — does it fall
        back to one ``embed()`` per anchor. That cold-start cost is paid at
        most once per anchor name: ``embed()`` writes L1+L2, and every
        subsequent ``serve()`` hits L1 (the anchor is re-touched each call,
        so it stays hot). There is no prewarm hook for interest-anchor names
        because the engine does not hold the soul profile; the lookup-first
        path plus L1 stickiness keeps the steady-state hot path network-free
        without one.
        """
        embedding = self._embedding_service
        if embedding is None:
            return []
        active = getattr(embedding, "image_embedding_active", None)
        if not (callable(active) and active()):
            return []

        anchors: list[str] = []
        seen: set[str] = set()
        for interest in _interests_by_weight(profile)[:_VISUAL_COVER_MAX_ANCHORS]:
            name = str(getattr(interest, "name", "") or "").strip()
            if name and name not in seen:
                seen.add(name)
                anchors.append(name)
        if not anchors:
            return []

        lookup = getattr(embedding, "lookup_cached", None)
        vectors: list[list[float]] = []
        for anchor in anchors:
            # Cache-first: L1 → L2, no provider call. Steady state is pure L1.
            vec: list[float] = []
            if callable(lookup):
                vec = lookup(anchor) or []
            if not vec:
                # Cold miss only — embed once, then L1/L2 caches it for every
                # future serve(). Best-effort: a failure just skips this anchor.
                try:
                    vec = await embedding.embed(anchor)
                except Exception:
                    logger.debug("visual anchor embed failed for %r", anchor, exc_info=True)
                    continue
            if vec:
                vectors.append(vec)
        return vectors

    async def _visual_cover_bonus(
        self,
        candidate: DiscoveredContent,
        anchor_vecs: list[list[float]],
        *,
        allow_fetch: bool = True,
    ) -> float:
        """Bounded additive visual bonus from cover↔interest alignment.

        Reuses the discovery-warmed image vector (URL-keyed) so it's a cache
        lookup. ``allow_fetch=True`` (delight background path) may pay one cold
        fetch+embed on a miss; ``allow_fetch=False`` (the latency-sensitive
        ``serve()`` hot path) must stay lookup-only and contributes 0 on a miss
        — the warmer fills the cache for the next batch. Returns 0.0 whenever
        anything is missing/inactive — never raises, never negative. See the
        ``_VISUAL_COVER_*`` calibration note for why this stays small.
        """
        if not anchor_vecs:
            return 0.0
        embedding = self._embedding_service
        if embedding is None:
            return 0.0
        embed_image = getattr(embedding, "embed_image", None)
        if not callable(embed_image):
            return 0.0
        cover_url = str(getattr(candidate, "cover_url", "") or "").strip()
        if not cover_url:
            return 0.0

        from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

        cache_key = image_embedding_cache_key_for_url(cover_url)
        cover_vec: list[float] = []
        lookup = getattr(embedding, "lookup_cached_image", None)
        if callable(lookup):
            cover_vec = lookup(cache_key) or []
        if not cover_vec and allow_fetch:
            # Cold miss (warmer hasn't run / cache evicted): fetch + embed once.
            # Only the background path allows this — never on the serve() hot path.
            try:
                from openbiliclaw.discovery.multimodal import (
                    prepare_cover_bytes_for_embedding,
                )

                prepared = await prepare_cover_bytes_for_embedding(
                    cover_url, max_px=384, quality=72, timeout_seconds=6
                )
                if prepared is None:
                    return 0.0
                image_bytes, mime_type = prepared
                cover_vec = await embed_image(image_bytes, mime_type=mime_type, cache_key=cache_key)
            except Exception:
                logger.debug("visual cover embed failed for %s", candidate.bvid, exc_info=True)
                return 0.0
        if not cover_vec:
            return 0.0

        return self._cover_bonus_from_vec(cover_vec, anchor_vecs)

    @staticmethod
    def _cover_bonus_from_vec(
        cover_vec: list[float],
        anchor_vecs: list[list[float]],
    ) -> float:
        """Map cover↔anchor cross-modal cosine to the bounded additive bonus."""
        if not cover_vec or not anchor_vecs:
            return 0.0
        from openbiliclaw.llm.embedding import cosine_similarity

        max_sim = 0.0
        for anchor_vec in anchor_vecs:
            max_sim = max(max_sim, cosine_similarity(cover_vec, anchor_vec))
        span = _VISUAL_COVER_SIM_CEIL - _VISUAL_COVER_SIM_FLOOR
        if span <= 0:
            return 0.0
        norm = max(0.0, min(1.0, (max_sim - _VISUAL_COVER_SIM_FLOOR) / span))
        return _VISUAL_COVER_BONUS_MAX * norm

    async def _visual_bonus_map(
        self,
        candidates: list[DiscoveredContent],
        profile: SoulProfile,
    ) -> dict[str, float]:
        """Per-candidate cover-visual bonus for the serve() ranking (lookup-only).

        Returns ``{}`` — a no-op that leaves the ranking byte-identical — when
        multimodal embedding is inactive, the profile has no interest anchors,
        or too few cover-bearing candidates are warmed yet (fairness guard, see
        ``_VISUAL_COVER_MIN_COVERAGE``). Never fetches a cover on this hot path:
        a warm miss contributes no bonus and the pool-cover prewarm backfills it.
        """
        anchor_vecs = await self._visual_anchor_vectors(profile)
        if not anchor_vecs:
            return {}
        embedding = self._embedding_service
        lookup = getattr(embedding, "lookup_cached_image", None)
        if not callable(lookup):
            return {}

        from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

        with_cover = 0
        warmed = 0
        bonuses: dict[str, float] = {}
        for candidate in candidates:
            bvid = str(getattr(candidate, "bvid", "") or "")
            cover_url = str(getattr(candidate, "cover_url", "") or "").strip()
            if not bvid or not cover_url:
                continue
            with_cover += 1
            cover_vec = lookup(image_embedding_cache_key_for_url(cover_url)) or []
            if not cover_vec:
                continue
            warmed += 1
            bonus = self._cover_bonus_from_vec(cover_vec, anchor_vecs)
            if bonus > 0.0:
                bonuses[bvid] = bonus
        # Fairness guard: withhold the bonus for the whole batch until enough
        # cover-bearing candidates are warmed, so a half-backfilled pool doesn't
        # tilt the ranking toward freshly-discovered items over older ones.
        if with_cover == 0 or warmed / with_cover < _VISUAL_COVER_MIN_COVERAGE:
            return {}
        return bonuses

    def _cover_embedding_active(self) -> bool:
        active = getattr(self._embedding_service, "image_embedding_active", None)
        return bool(callable(active) and active())

    def _visual_profile_active(self) -> bool:
        """True only when the flag is on AND image embedding is active."""
        return self._visual_profile_enabled and self._cover_embedding_active()

    def _load_visual_profile_cache(self) -> list[dict[str, Any]]:
        """Lazily load centroids from the DB into ``_visual_profile_cache``.

        Returns the cached list (possibly empty). Idempotent — once loaded, the
        hot path reads memory only. ``rebuild_visual_profile`` refreshes it.

        On a transient DB error (e.g. "database is locked") the cache is left
        ``None`` so the NEXT call retries the load — previously it was set to
        ``[]``, and since the guard is ``is None`` (``[] is not None``) a single
        first-load error silently disabled P1 AND P3 (which shares this cache)
        for the whole process lifetime, recoverable only by new feedback
        triggering a rebuild. Logged at WARNING so the silent disable is
        observable (pitfall rule 7: diagnosable beats "appears to work").
        """
        if self._visual_profile_cache is None:
            try:
                self._visual_profile_cache = self._database.get_user_visual_clusters()
            except Exception:
                logger.warning("visual profile load failed; will retry next call", exc_info=True)
                # Leave None — do NOT cache [] (would permanently disable P1/P3).
        return self._visual_profile_cache or []

    async def rebuild_visual_profile(self) -> int:
        """Rebuild liked/disliked cover centroids from recommendation feedback.

        Queries feedback rows (like/dislike/save) → embeds each cover (URL-keyed
        L2 hit when discovery already warmed it; cold-fetch+embed otherwise) →
        greedy agglomerative clustering into mean centroids → atomically
        replaces ``user_visual_clusters`` → refreshes the in-memory cache.

        Best-effort: never raises. Returns the number of centroids stored.
        Skipped (returns 0) when the feature is off, image embedding is
        inactive, or there is no feedback yet. Throttled by the caller
        (only fires when feedback is newer than the last rebuild).
        """
        if not self._visual_profile_active():
            return 0
        embedding = self._embedding_service
        embed_image = getattr(embedding, "embed_image", None)
        if not callable(embed_image):
            return 0

        from openbiliclaw.discovery.multimodal import prepare_cover_bytes_for_embedding
        from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url
        from openbiliclaw.recommendation.visual_profile import (
            build_centroids,
            cross_clean_labels,
        )

        # feedback rows: {bvid, cover_url, feedback_type, ...}. Use the
        # dedicated feedback-cover query, NOT get_recommendations — that one
        # applies the pool admission predicate (confidence >= min_score) and
        # silently dropped low-confidence feedback rows, so a rebuild saw
        # only a fraction of the feedback and built too few centroids.
        fetch_covers = getattr(self._database, "get_feedback_covers", None)
        rows = (
            fetch_covers(limit=500)
            if callable(fetch_covers)
            else self._database.get_recommendations(
                limit=500, exclude_processed=False
            )
        )
        pos_urls: list[str] = []
        neg_urls: list[str] = []
        for row in rows:
            ftype = str(row.get("feedback_type", "") or "").strip()
            cover_url = str(row.get("cover_url", "") or "").strip()
            if not cover_url:
                continue
            if ftype == "dislike":
                neg_urls.append(cover_url)
            elif ftype in ("like", "save"):
                pos_urls.append(cover_url)

        async def _vecs_for(urls: list[str]) -> list[list[float]]:
            lookup = getattr(embedding, "lookup_cached_image", None)
            out: list[list[float]] = []
            for url in urls:
                key = image_embedding_cache_key_for_url(url)
                vec: list[float] = []
                if callable(lookup):
                    vec = lookup(key) or []
                if not vec:
                    try:
                        prepared = await prepare_cover_bytes_for_embedding(
                            url, max_px=384, quality=72, timeout_seconds=6
                        )
                    except Exception:
                        logger.debug(
                            "visual profile cover prepare failed: %s",
                            url[:80], exc_info=True,
                        )
                        continue
                    if prepared is None:
                        continue
                    image_bytes, mime_type = prepared
                    try:
                        vec = await embed_image(image_bytes, mime_type=mime_type, cache_key=key)
                    except Exception:
                        logger.debug(
                            "visual profile embed_image failed: %s",
                            url[:80], exc_info=True,
                        )
                        continue
                if vec:
                    out.append(vec)
            return out

        pos_vecs = await _vecs_for(pos_urls)
        neg_vecs = await _vecs_for(neg_urls)
        # Cross-clean label noise BEFORE clustering: drop liked/disliked covers
        # that sit in the enemy's territory (misclicks / love-hate contradictions).
        # Done before clustering so noise can't pollute a centroid first. The
        # dropped covers are NOT flipped to the opposite polarity — just removed
        # from centroid construction (kept raw in the feedback table for later
        # hard-negative use). Conservative drop_margin: on small feedback sets a
        # single drop shifts the centroid a lot, so only clear cases are pruned.
        cleaned = cross_clean_labels(pos_vecs, neg_vecs, k=3, drop_margin=0.08)
        if cleaned.dropped_pos or cleaned.dropped_neg:
            logger.info(
                "Visual profile cross-clean dropped %d pos / %d neg covers (label noise "
                "in enemy territory); clustering %d pos / %d neg",
                len(cleaned.dropped_pos), len(cleaned.dropped_neg),
                len(cleaned.kept_pos), len(cleaned.kept_neg),
            )
        pos_clusters = build_centroids(cleaned.kept_pos)
        neg_clusters = build_centroids(cleaned.kept_neg)

        stored: list[dict[str, Any]] = []
        for c in pos_clusters:
            stored.append(
                {"polarity": "pos", "centroid": list(c.centroid), "member_count": c.member_count}
            )
        for c in neg_clusters:
            stored.append(
                {"polarity": "neg", "centroid": list(c.centroid), "member_count": c.member_count}
            )

        # Guard against wiping a previously-valid profile on a transient
        # failure: if there IS feedback (pos_urls or neg_urls non-empty) but
        # this rebuild produced zero clusters (all cover embeds failed, or
        # every liked cover was a dissimilar singleton pruned by min_members),
        # do NOT call replace_user_visual_clusters([]) — that DELETEs the
        # whole table and destroys existing centroids, causing the profile to
        # appear/disappear/reappear. Only wipe when there is genuinely no
        # feedback at all. The in-memory cache is left untouched too, so the
        # hot path keeps using the last good centroids until a rebuild
        # succeeds (pitfall rule 2: never persist failed/empty results).
        has_feedback = bool(pos_urls or neg_urls)
        if not stored and has_feedback:
            logger.warning(
                "Visual profile rebuild produced 0 centroids from %d pos / %d neg "
                "feedback covers (transient embed failure or all singletons); "
                "preserving existing %d centroids",
                len(pos_urls), len(neg_urls),
                len(self._visual_profile_cache or []),
            )
            return 0

        try:
            self._database.replace_user_visual_clusters(stored)
            self._visual_profile_cache = self._database.get_user_visual_clusters()
        except Exception:
            logger.debug("visual profile persist failed", exc_info=True)
            return 0
        logger.info(
            "Visual profile rebuilt: %d pos / %d neg centroids (from %d/%d feedback covers)",
            len(pos_clusters), len(neg_clusters), len(pos_vecs), len(neg_vecs),
        )
        return len(stored)

    @staticmethod
    def _visual_profile_bonus_from_vec(
        cover_vec: list[float],
        pos_centroids: list[list[float]],
        neg_centroids: list[list[float]],
        contested: set[tuple[int, int]],
    ) -> float:
        """Margin-score a cover against the pos/neg centroids (signed).

        Same-modal image↔image cosine. The cover modality cannot distinguish
        like/dislike everywhere (the user's liked/disliked covers are visually
        interleaved — see ``_VISUAL_PROFILE_*`` calibration), so this abstains
        where the cover has no say and acts only where it clearly separates:

        - Find the best pos centroid (i) and best neg centroid (j).
        - If (i, j) is a CONTESTED pair (cosine >= _VISUAL_PROFILE_CONTESTED),
          the candidate sits in a love-hate region: the centroids overlap, so
          the bar to trust the cover is HIGHER. Require |net| >=
          _VISUAL_PROFILE_CONTESTED_MARGIN (2x the normal margin) to act; the
          ambiguous middle grays out. A candidate with a CLEAR net still speaks
          — the contested region is skeptical, not mute (graying a clear win
          just because the centroids overlap threw away ~40% of P3's signal).
        - Else the normal margin applies: s_pos - s_neg >= margin → boost,
          s_neg - s_pos >= margin → suppress (negative).
        - Else (|net| < the applicable margin) → gray.

        Returns a SIGNED float in [-_SUPPRESS_MAX, +_BOOST_MAX]. The neg
        centroids are used here (unlike the prior pure-pos design) because the
        contested + margin geometry is what makes them safe: a neg match only
        suppresses when it clearly wins past the (possibly raised) margin.
        """
        if not cover_vec:
            return 0.0
        if not pos_centroids and not neg_centroids:
            return 0.0
        from openbiliclaw.llm.embedding import cosine_similarity

        def _best_idx(centroids: list[list[float]]) -> tuple[int, float]:
            best_i, best_s = -1, 0.0
            for idx, c in enumerate(centroids):
                sim = cosine_similarity(cover_vec, c)
                if sim > best_s:
                    best_s, best_i = sim, idx
            return best_i, best_s

        i, s_pos = _best_idx(pos_centroids) if pos_centroids else (-1, 0.0)
        j, s_neg = _best_idx(neg_centroids) if neg_centroids else (-1, 0.0)

        # Contested region: best pos/neg centroids are a love-hate pair. Raise
        # the margin (don't mute) — a candidate with a clear net still speaks.
        contested_region = i >= 0 and j >= 0 and (i, j) in contested
        margin = _VISUAL_PROFILE_CONTESTED_MARGIN if contested_region else _VISUAL_PROFILE_MARGIN

        net = s_pos - s_neg
        if net >= margin:
            # Boost: map [margin, net_p95] -> [0, BOOST_MAX].
            span = _VISUAL_PROFILE_NET_P95 - margin
            if span <= 0:
                return _VISUAL_PROFILE_BOOST_MAX
            return _VISUAL_PROFILE_BOOST_MAX * min(1.0, (net - margin) / span)
        if -net >= margin:
            # Suppress: map [margin, -net_p5] -> [0, -SUPPRESS_MAX].
            span = (-_VISUAL_PROFILE_NET_P5) - margin
            if span <= 0:
                return -_VISUAL_PROFILE_SUPPRESS_MAX
            return -_VISUAL_PROFILE_SUPPRESS_MAX * min(1.0, (-net - margin) / span)
        return 0.0

    async def _visual_profile_bonus_map(
        self,
        candidates: list[DiscoveredContent],
    ) -> dict[str, float]:
        """Per-candidate visual-profile bonus for serve() (lookup-only, parallel).

        Returns ``{}`` — a no-op that leaves the ranking byte-identical — when
        the feature is off, image embedding is inactive, no centroids are
        loaded yet, or the fairness coverage guard trips. Never fetches a cover
        on this hot path: a warm miss contributes no bonus and the background
        rebuild + pool-cover prewarm backfill it.
        """
        if not self._visual_profile_active():
            return {}
        embedding = self._embedding_service
        lookup = getattr(embedding, "lookup_cached_image", None)
        if not callable(lookup):
            return {}

        from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url
        from openbiliclaw.recommendation.visual_profile import (
            VisualCluster,
            contested_pairs,
        )

        cache = self._load_visual_profile_cache()
        if not cache:
            return {}
        pos_rows = [r for r in cache if r.get("polarity") == "pos"]
        neg_rows = [r for r in cache if r.get("polarity") == "neg"]
        pos_centroids = [r["centroid"] for r in pos_rows]
        neg_centroids = [r["centroid"] for r in neg_rows]
        if not pos_centroids and not neg_centroids:
            return {}

        # Contested pairs: pos/neg centroid index pairs whose cosine >= threshold
        # (love-hate regions where the cover has no say). Computed once per batch.
        contested = contested_pairs(
            [VisualCluster(tuple(r["centroid"]), int(r.get("member_count", 1))) for r in pos_rows],
            [VisualCluster(tuple(r["centroid"]), int(r.get("member_count", 1))) for r in neg_rows],
            threshold=_VISUAL_PROFILE_CONTESTED,
        )

        with_cover = 0
        warmed = 0
        bonuses: dict[str, float] = {}
        for candidate in candidates:
            bvid = str(getattr(candidate, "bvid", "") or "")
            cover_url = str(getattr(candidate, "cover_url", "") or "").strip()
            if not bvid or not cover_url:
                continue
            with_cover += 1
            cover_vec = lookup(image_embedding_cache_key_for_url(cover_url)) or []
            if not cover_vec:
                continue
            warmed += 1
            bonus = self._visual_profile_bonus_from_vec(
                cover_vec, pos_centroids, neg_centroids, contested
            )
            # Signed: keep both boosts (>0) and suppressions (<0). A zero means
            # the candidate is in the contested/gray band — no entry, no nudge.
            if abs(bonus) > 0.0:
                bonuses[bvid] = bonus
        # Fairness guard (same rationale as _visual_bonus_map): withhold the
        # bonus for the whole batch until enough cover-bearing candidates are
        # warmed, so a half-backfilled pool doesn't tilt toward fresh items.
        if with_cover == 0 or warmed / with_cover < _VISUAL_COVER_MIN_COVERAGE:
            return {}
        return bonuses

    def _keyframe_active(self) -> bool:
        """True only when the flag is on AND image embedding is active."""
        return self._keyframe_enabled and self._cover_embedding_active()

    @staticmethod
    def _normalize_bonus_per_platform(
        candidates: list[DiscoveredContent],
        combined_bonus: dict[str, float],
    ) -> dict[str, float]:
        """Rescale the stacked bonus per-platform to [-cap, +cap] (signed).

        Without this, platforms missing a signal (bangumi/xhs have no danmaku
        or keyframes) are structurally shorter on combined_bonus than Bilibili
        and get squeezed out of the top purely on height — observed when P2
        opened: bangumi dropped from 3 to 1 in the top-25, not because its
        candidates were worse but because Bilibili rows gained a +0.05 channel
        they could never match.

        Per-platform min-max normalization fixes the height: within each
        platform's own pool, the strongest candidate's bonus is stretched to
        ``+cap`` and the weakest to ``-cap``. A platform that lacks a signal
        only loses *intra-platform* discrimination for it (its rows still
        spread across the same [-cap, +cap] range by their remaining signals),
        not *cross-platform* height.

        SIGNED because the visual signals (P1/P3) now use margin scoring that
        can suppress (negative) as well as boost. Mapping the full [g_min,
        g_max] range to [-cap, +cap] keeps a gray/zero candidate near 0 when
        the platform's range is roughly symmetric, and levels both the top
        (boost) and bottom (suppress) per platform.

        A platform group whose max == min (all equal, including all-zero) is
        left as-is — stretching a zero span would NaN, and an all-equal group
        carries no intra-platform information to rescale anyway. Empty input
        returns empty, so the all-signals-off / no-centroids case stays
        byte-identical (no bonus keys -> no normalization -> empty dict).
        """
        if not combined_bonus:
            return combined_bonus
        # bvid -> platform, defaulting bilibili (the historical single-platform
        # behavior) so a missing/blank platform never forms its own singleton
        # group and change the ranking for pre-existing rows.
        platform_of: dict[str, str] = {}
        for cand in candidates:
            bvid = str(getattr(cand, "bvid", "") or "")
            if not bvid:
                continue
            platform = str(getattr(cand, "source_platform", "") or "").strip() or "bilibili"
            platform_of[bvid] = platform

        # Group ALL bonus values (incl. negative suppressions) by platform.
        groups: dict[str, list[float]] = {}
        for bvid, bonus in combined_bonus.items():
            groups.setdefault(platform_of.get(bvid, "bilibili"), []).append(bonus)

        if not groups:
            return combined_bonus

        cap = _COMBINED_BONUS_CAP
        normalized: dict[str, float] = dict(combined_bonus)
        for platform, vals in groups.items():
            g_max = max(vals)
            g_min = min(vals)
            span = g_max - g_min
            if span <= 0.0:
                # All bonuses in this platform are equal (incl. all-zero) — no
                # intra-platform information to rescale; leave as-is.
                continue
            for bvid, bonus in combined_bonus.items():
                if platform_of.get(bvid, "bilibili") != platform:
                    continue
                # Map [g_min, g_max] -> [-cap, +cap].
                normalized[bvid] = cap * (2.0 * (bonus - g_min) / span - 1.0)
        return normalized

    def _keyframe_bonus_from_vecs(
        self,
        frame_vecs: list[list[float]],
        pos_centroids: list[list[float]],
        neg_centroids: list[list[float]],
        contested: set[tuple[int, int]],
    ) -> float:
        """Margin-score max-pooled frames against the pos/neg centroids (signed).

        Max-pool (not mean): the question is "does ANY sampled frame look like
        the user's taste", and a mean would wash out one strong match among
        several unremarkable frames. Mirrors :meth:`_visual_profile_bonus_from_vec`
        but on its own ``_KEYFRAME_*`` scale — keyframes are downscaled stills
        whose net range is tighter than full-size covers.

        Same margin design as P1 (see _visual_profile_bonus_from_vec): find the
        best pos (i) and best neg (j) centroid across all frames; if (i,j) is a
        contested pair, raise the margin (don't mute) — a clear net still speaks;
        else boost/suppress on the net past the normal margin. The neg centroids
        are used (the contested + margin geometry makes them safe) — P3 reuses
        P1's centroids and its contested set. Returns a SIGNED float in
        [-_SUPPRESS_MAX, +_BOOST_MAX].
        """
        if not frame_vecs or (not pos_centroids and not neg_centroids):
            return 0.0
        from openbiliclaw.llm.embedding import cosine_similarity

        # Best pos/neg centroid across all frames (max-pool), keeping the index
        # that achieved it so we can test the contested pair.
        best_pos_i, best_pos_s = -1, 0.0
        for fv in frame_vecs:
            if not fv:
                continue
            for idx, c in enumerate(pos_centroids):
                sim = cosine_similarity(fv, c)
                if sim > best_pos_s:
                    best_pos_s, best_pos_i = sim, idx
        best_neg_j, best_neg_s = -1, 0.0
        for fv in frame_vecs:
            if not fv:
                continue
            for idx, c in enumerate(neg_centroids):
                sim = cosine_similarity(fv, c)
                if sim > best_neg_s:
                    best_neg_s, best_neg_j = sim, idx

        contested_region = (
            best_pos_i >= 0 and best_neg_j >= 0 and (best_pos_i, best_neg_j) in contested
        )
        margin = _KEYFRAME_CONTESTED_MARGIN if contested_region else _KEYFRAME_MARGIN

        net = best_pos_s - best_neg_s
        if net >= margin:
            span = _KEYFRAME_NET_P95 - margin
            if span <= 0:
                return _KEYFRAME_BOOST_MAX
            return _KEYFRAME_BOOST_MAX * min(1.0, (net - margin) / span)
        if -net >= margin:
            span = (-_KEYFRAME_NET_P5) - margin
            if span <= 0:
                return -_KEYFRAME_SUPPRESS_MAX
            return -_KEYFRAME_SUPPRESS_MAX * min(1.0, (-net - margin) / span)
        return 0.0

    async def _keyframe_bonus_map(
        self,
        candidates: list[DiscoveredContent],
    ) -> dict[str, float]:
        """Per-candidate keyframe bonus for serve() (lookup-only, parallel).

        Reuses the P1 visual centroids: the same taste profile, now matched
        against what the video actually looks like rather than its cover.
        Returns ``{}`` — leaving the ranking byte-identical — when the feature
        is off, image embedding is inactive, or no centroids exist yet. Never
        fetches on this hot path; ``prewarm_pool_keyframes`` fills the cache.
        """
        if not self._keyframe_active():
            return {}
        embedding = self._embedding_service
        lookup = getattr(embedding, "lookup_cached_image", None)
        if not callable(lookup):
            return {}

        from openbiliclaw.llm.embedding import keyframe_embedding_cache_key
        from openbiliclaw.recommendation.visual_profile import (
            VisualCluster,
            contested_pairs,
        )

        cache = self._load_visual_profile_cache()
        if not cache:
            return {}
        pos_rows = [r for r in cache if r.get("polarity") == "pos"]
        neg_rows = [r for r in cache if r.get("polarity") == "neg"]
        pos_centroids = [r["centroid"] for r in pos_rows]
        neg_centroids = [r["centroid"] for r in neg_rows]
        if not pos_centroids and not neg_centroids:
            return {}

        # P3 shares P1's centroids and contested set (same geometry).
        contested = contested_pairs(
            [VisualCluster(tuple(r["centroid"]), int(r.get("member_count", 1))) for r in pos_rows],
            [VisualCluster(tuple(r["centroid"]), int(r.get("member_count", 1))) for r in neg_rows],
            threshold=_KEYFRAME_CONTESTED,
        )

        bonuses: dict[str, float] = {}
        for candidate in candidates:
            bvid = str(getattr(candidate, "bvid", "") or "")
            if not bvid:
                continue
            frame_vecs: list[list[float]] = []
            for frame_index in range(self._keyframe_max_frames):
                vec = lookup(keyframe_embedding_cache_key(bvid, frame_index)) or []
                if vec:
                    frame_vecs.append(vec)
            if not frame_vecs:
                continue
            bonus = self._keyframe_bonus_from_vecs(
                frame_vecs, pos_centroids, neg_centroids, contested
            )
            if abs(bonus) > 0.0:
                bonuses[bvid] = bonus
        return bonuses

    async def prewarm_pool_keyframes(self, *, limit: int = 50) -> int:
        """Fetch + embed video keyframes for pool rows that lack them.

        Bilibili pre-generates keyframe sprite sheets, so this costs one small
        JPEG per video rather than a video download. Best-effort: every failure
        path still stamps ``keyframes_fetched_at`` so a video without videoshot
        data is not retried every cycle. Returns the number of videos processed.
        """
        if not self._keyframe_active():
            return 0
        embedding = self._embedding_service
        embed_image = getattr(embedding, "embed_image", None)
        if not callable(embed_image):
            return 0

        needing = getattr(self._database, "get_candidates_needing_keyframes", None)
        mark = getattr(self._database, "mark_keyframes_fetched", None)
        if not callable(needing) or not callable(mark):
            return 0
        try:
            rows = needing(limit=limit)
        except Exception:
            logger.debug("get_candidates_needing_keyframes failed", exc_info=True)
            return 0
        if not rows:
            return 0

        from openbiliclaw.discovery.keyframes import fetch_keyframes
        from openbiliclaw.llm.embedding import keyframe_embedding_cache_key

        processed = 0
        # Serial, like _prewarm_pool_covers: Bilibili rate-limits far more
        # aggressively than the embedding backend, so fan-out is the wrong
        # trade here even though each item is cheap.
        for row in rows:
            bvid = str(row.get("bvid") or "").strip()
            if not bvid:
                continue
            embedded = 0
            try:
                frames = await fetch_keyframes(bvid, max_frames=self._keyframe_max_frames)
                for frame_index, frame_bytes in enumerate(frames):
                    cache_key = keyframe_embedding_cache_key(bvid, frame_index)
                    vector = await embed_image(
                        frame_bytes, mime_type="image/jpeg", cache_key=cache_key
                    )
                    if vector:
                        embedded += 1
            except Exception:
                logger.debug("keyframe prewarm failed for %s", bvid, exc_info=True)
            try:
                # Only stamp when the result is definitive, so a TRANSIENT embed
                # backend failure (frames were fetched but every embed_image
                # returned []) is not persisted as "done" — that would permanently
                # exclude the video from P3 re-prewarm (get_candidates_needing_keyframes
                # filters keyframes_fetched_at IS NULL). Two cases are definitive:
                #   - frames == []: the video genuinely has no videoshot data
                #     (permanent) — stamp so it is not re-fetched every cycle.
                #   - embedded > 0: at least one frame embedded — stamp the count.
                # frames non-empty but embedded == 0 means a transient embed
                # outage: leave keyframes_fetched_at NULL so it retries next
                # cycle (pitfall rule 2: never persist failed/empty results).
                if not frames or embedded > 0:
                    mark(bvid, keyframe_count=embedded)
            except Exception:
                logger.debug("mark_keyframes_fetched failed for %s", bvid, exc_info=True)
            processed += 1
        if processed:
            logger.info("Keyframe prewarm processed %d video(s)", processed)
        return processed

    def _danmaku_active(self) -> bool:
        """True when the flag is on and an embedding service exists.

        Unlike the visual signals this needs no multimodal model — danmaku are
        text, so any embedding provider works.
        """
        return self._danmaku_enabled and self._embedding_service is not None

    async def _danmaku_bonus_map(
        self,
        candidates: list[DiscoveredContent],
        profile: SoulProfile,
    ) -> dict[str, float]:
        """Per-candidate danmaku bonus for serve() (candidate vectors lookup-only).

        Compares each candidate's stored danmaku summary against the profile's
        interest anchors (text↔text, same anchors the cover bonus embeds).
        Returns ``{}`` — leaving the ranking byte-identical — when the feature
        is off or nothing is stored yet. Candidate danmaku vectors are
        lookup-only (a cache miss contributes no bonus; the prewarm fills it).
        Anchor vectors are resolved cache-first too — see
        ``_danmaku_anchor_vectors`` for the cold-miss fallback (one embed per
        anchor, paid once then L1-cached).
        """
        if not self._danmaku_active():
            return {}
        embedding = self._embedding_service
        if embedding is None:
            return {}
        lookup = getattr(embedding, "lookup_cached", None)
        if not callable(lookup):
            return {}

        fetch = getattr(self._database, "get_danmaku_texts_for", None)
        if not callable(fetch):
            return {}
        bvids = [str(getattr(c, "bvid", "") or "") for c in candidates]
        try:
            stored = fetch([b for b in bvids if b])
        except Exception:
            logger.debug("danmaku text lookup failed", exc_info=True)
            return {}
        if not stored:
            return {}

        anchor_vecs = await self._danmaku_anchor_vectors(profile)
        if not anchor_vecs:
            return {}

        from openbiliclaw.llm.embedding import cosine_similarity

        bonuses: dict[str, float] = {}
        for candidate in candidates:
            bvid = str(getattr(candidate, "bvid", "") or "")
            text = stored.get(bvid, "")
            if not bvid or not text:
                continue
            vec = lookup(text) or []
            if not vec:
                continue
            max_sim = 0.0
            for anchor_vec in anchor_vecs:
                sim = cosine_similarity(vec, anchor_vec)
                if sim > max_sim:
                    max_sim = sim
            span = _DANMAKU_SIM_CEIL - _DANMAKU_SIM_FLOOR
            if span <= 0:
                continue
            norm = max(0.0, min(1.0, (max_sim - _DANMAKU_SIM_FLOOR) / span))
            bonus = _DANMAKU_BONUS_MAX * norm
            if bonus > 0.0:
                bonuses[bvid] = bonus
        return bonuses

    async def _danmaku_anchor_vectors(self, profile: SoulProfile) -> list[list[float]]:
        """Embed the profile's top interest names once for danmaku alignment.

        Same anchors as ``_visual_anchor_vectors`` but without the
        image-embedding gate — danmaku matching is text↔text, so it works on
        any embedding provider. Same cache-first hot-path contract: L1→L2
        lookup, cold-miss fallback to one ``embed()`` per anchor (paid once,
        then L1-cached for every future serve()).
        """
        embedding = self._embedding_service
        if embedding is None:
            return []
        anchors: list[str] = []
        seen: set[str] = set()
        for interest in _interests_by_weight(profile)[:_VISUAL_COVER_MAX_ANCHORS]:
            name = str(getattr(interest, "name", "") or "").strip()
            if name and name not in seen:
                seen.add(name)
                anchors.append(name)
        if not anchors:
            return []
        lookup = getattr(embedding, "lookup_cached", None)
        vectors: list[list[float]] = []
        for anchor in anchors:
            vec: list[float] = []
            if callable(lookup):
                vec = lookup(anchor) or []
            if not vec:
                try:
                    vec = await embedding.embed(anchor)
                except Exception:
                    logger.debug("danmaku anchor embed failed for %r", anchor, exc_info=True)
                    continue
            if vec:
                vectors.append(vec)
        return vectors

    async def prewarm_pool_danmaku(self, *, limit: int = 50) -> int:
        """Fetch, condense, store and embed danmaku for pool rows lacking them.

        Best-effort: every path still stamps ``danmaku_fetched_at`` so a video
        with no danmaku is not retried each cycle. Returns rows processed.
        """
        if not self._danmaku_active():
            return 0
        client = self._bilibili_client
        if client is None:
            return 0
        embedding = self._embedding_service
        if embedding is None:
            return 0

        needing = getattr(self._database, "get_candidates_needing_danmaku", None)
        update = getattr(self._database, "update_danmaku_text", None)
        if not callable(needing) or not callable(update):
            return 0
        try:
            rows = needing(limit=limit)
        except Exception:
            logger.debug("get_candidates_needing_danmaku failed", exc_info=True)
            return 0
        if not rows:
            return 0

        from openbiliclaw.discovery.danmaku import condense_danmaku

        processed = 0
        # Serial: Bilibili rate-limits far more aggressively than the embedding
        # backend, and the client's own _respect_rate_limit already paces us.
        for row in rows:
            bvid = str(row.get("bvid") or "").strip()
            if not bvid:
                continue
            condensed = ""
            try:
                info = await client.get_video_info(bvid)
                cid = int(getattr(info, "cid", 0) or 0)
                if cid > 0:
                    raw = await client.get_danmaku_texts(cid)
                    condensed = condense_danmaku(raw, max_chars=self._danmaku_max_chars)
                    if condensed:
                        # Text embeddings are keyed by the text itself, so the
                        # serve() lookup uses the very same stored string.
                        await embedding.embed(condensed)
            except Exception:
                logger.debug("danmaku prewarm failed for %s", bvid, exc_info=True)
            try:
                # Stamp even when empty — otherwise videos without danmaku are
                # re-fetched on every prewarm cycle.
                update(bvid, danmaku_text=condensed)
            except Exception:
                logger.debug("update_danmaku_text failed for %s", bvid, exc_info=True)
            processed += 1
        if processed:
            logger.info("Danmaku prewarm processed %d video(s)", processed)
        return processed

    async def _prewarm_pool_covers(self, candidates: list[DiscoveredContent]) -> int:
        """Backfill cover embeddings for content already in the pool (URL-keyed).

        Companion to discovery's per-admission ``_warm_cover_embeddings``: covers
        the case where multimodal was enabled AFTER items were pooled, so the
        lookup-only ``serve()`` path and delight treat old and new content
        consistently instead of silently favouring freshly-discovered covers.
        Idempotent (skips already-warmed keys), best-effort, no-op when off.
        """
        if not candidates or not self._cover_embedding_active():
            return 0
        embed_image = getattr(self._embedding_service, "embed_image", None)
        lookup = getattr(self._embedding_service, "lookup_cached_image", None)
        if not callable(embed_image):
            return 0

        from openbiliclaw.discovery.multimodal import prepare_cover_bytes_for_embedding
        from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

        warmed = 0
        for candidate in candidates:
            cover_url = str(getattr(candidate, "cover_url", "") or "").strip()
            if not cover_url:
                continue
            key = image_embedding_cache_key_for_url(cover_url)
            if callable(lookup) and lookup(key):
                continue  # already warm — idempotent
            try:
                prepared = await prepare_cover_bytes_for_embedding(
                    cover_url, max_px=384, quality=72, timeout_seconds=6
                )
                if prepared is None:
                    continue
                image_bytes, mime_type = prepared
                vec = await embed_image(image_bytes, mime_type=mime_type, cache_key=key)
                if vec:
                    warmed += 1
            except Exception:
                logger.debug("pool cover prewarm failed for %s", candidate.bvid, exc_info=True)
        if warmed:
            logger.info("Pool cover prewarm: warmed %d cover embedding(s)", warmed)
        return warmed

    @staticmethod
    def _evo_delight_reason(item: DiscoveredContent) -> str:
        return (item.pool_expression or "").strip()

    @staticmethod
    def _evo_delight_hook(item: DiscoveredContent) -> str:
        return (item.pool_topic_label or "").strip()

    async def _precompute_batch(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
        *,
        fallback_to_single: bool = True,
    ) -> int:
        """Generate expressions for a batch via one LLM call."""
        from openbiliclaw.llm.prompts import build_batch_expression_prompt

        tone_profile = build_tone_profile(
            profile=profile,
            preference_summary={
                "exploration_openness": profile.preferences.exploration_openness,
            },
            recent_feedback=[],
        )
        content_items = [
            {
                "bvid": item.bvid,
                "content_id": item.content_id or item.bvid,
                "title": item.title,
                "up_name": item.up_name,
                "description": (item.description or "")[:400],
                "source_strategy": item.source_strategy,
                "style_key": normalize_style_key(item.style_key),
                "topic_group": item.topic_group,
                "relevance_score": item.relevance_score,
                "content_type": item.content_type,
                "body_text": item.body_text,
            }
            for item in batch
        ]
        profile_summary = _recommendation_profile_summary(profile)
        messages = build_batch_expression_prompt(
            profile_summary=profile_summary,
            profile_blocks=self._profile_blocks(profile_summary, cache_key="batch_expression"),
            content_items=content_items,
            tone_profile=tone_profile,
            source_platform=batch[0].source_platform if batch else "bilibili",
        )

        complete_structured = self._llm.complete_structured_task
        try:
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=8192,
                # v0.3.51+: expression generation is short copy
                # writing per item — reasoning chain just bloats
                # output (write_expression cost ~3x with reasoning
                # vs without, no quality difference).
                reasoning_effort="",
                caller="recommendation.write_expression",
                **without_core_memory_kwargs(complete_structured),
            )
        except Exception as exc:
            kind = classify_llm_failure_kind(exc)
            if kind in {"rate_limited", "timeout", "connection", "server_error"}:
                raise ExpressionCopyTransientError(
                    kind=kind,
                    retry_after=self._retry_after_seconds(exc),
                ) from exc
            raise

        payload = extract_llm_json_list(
            str(response.content),
            wrapper_keys=("results", "items", "expressions", "data"),
            allow_singleton=True,
            item_predicate=lambda item: "expression" in item or "topic_label" in item,
        )
        if payload is None:
            raise ExpressionBatchMalformed(tuple(batch))

        payload_by_id = _batch_results_by_content_key(payload, batch)
        if payload_by_id is None and len(batch) > 1:
            # The prompt requires every entry to echo back its bvid /
            # content_id (rule 2) and preserve input order (rule 1). When a
            # *multi-item* response carries no identifiers we cannot verify
            # alignment: a reordered or repeated array silently attaches each
            # video the wrong (or an identical) reason. Weak local models
            # (e.g. qwen:7b under a truncated context window) hit this
            # constantly, surfacing to users as "every recommendation reason
            # is the same and doesn't match the video". Regenerate per item
            # instead — each single call carries exactly one content item and
            # cannot be misaligned. (A 1-item batch has no ordering ambiguity,
            # so positional matching below stays safe for it.)
            raise ExpressionBatchMalformed(tuple(batch))

        # Gather candidates first (keyed match, or positional for a lone item
        # where order is unambiguous) so we can reject a degenerate batch that
        # repeats the same expression across distinct videos (violates rule 6;
        # surfaces as identical 推荐语). Serving duplicate copy for different
        # videos is worse than serving none — the pool gate simply skips the
        # un-copied items until a healthier regeneration fills them.
        gathered: list[tuple[DiscoveredContent, str, str]] = []
        for i, item in enumerate(batch):
            if payload_by_id is None:
                result = payload[i] if i < len(payload) else None
            else:
                result = next(
                    (
                        payload_by_id[key]
                        for key in _content_result_keys(item)
                        if key in payload_by_id
                    ),
                    None,
                )
            if not isinstance(result, dict):
                continue
            validated = _validated_expression_fields(result, content_key=item.bvid)
            if validated is None:
                # Leave this item missing so the caller retries it rather than
                # persisting a repr'd payload as card copy.
                continue
            expression, topic_label = validated
            gathered.append((item, expression, topic_label))

        bvids_by_expression: dict[str, set[str]] = defaultdict(set)
        for item, expression, _ in gathered:
            bvids_by_expression[expression].add(item.bvid)
        duplicated = {
            expression for expression, bvids in bvids_by_expression.items() if len(bvids) > 1
        }
        if duplicated:
            logger.warning(
                "Batch expression produced %d expression(s) shared across "
                "distinct videos (model likely repeating itself); dropping them",
                len(duplicated),
            )

        completed = 0
        for item, expression, topic_label in gathered:
            if expression in duplicated:
                continue
            self._database.update_pool_copy(
                item.bvid,
                expression=expression,
                topic_label=topic_label,
            )
            item.pool_expression = expression
            item.pool_topic_label = topic_label
            completed += 1
        completed_keys = {
            item.bvid for item, expression, _ in gathered if expression not in duplicated
        }
        missing_items = tuple(item for item in batch if item.bvid not in completed_keys)
        if missing_items:
            raise ExpressionBatchMalformed(missing_items, completed)
        return completed

    async def _precompute_batch_with_split_retry(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
        max_split_depth: int = 3,
        max_extra_requests: int = 6,
    ) -> int:
        """Try a batch, split failed large batches, then fall back to singles.

        Split retries run inside the current expression worker. They do not
        create nested tasks, so ``expression_batch_concurrency`` remains the
        single concurrency control point.
        """
        budget = {"remaining": max(0, int(max_extra_requests))}

        async def run(items: list[DiscoveredContent], depth: int) -> int:
            try:
                return await self._precompute_batch(items, profile, fallback_to_single=False)
            except ExpressionBatchMalformed as exc:
                completed = exc.completed
                missing = list(exc.missing_items)
                if len(missing) <= 1 or depth >= max_split_depth or budget["remaining"] <= 0:
                    return completed
                subsets: tuple[list[DiscoveredContent], ...]
                if completed > 0:
                    subsets = (missing,)
                else:
                    midpoint = max(1, len(missing) // 2)
                    subsets = (missing[:midpoint], missing[midpoint:])
                total = completed
                for subset in subsets:
                    if not subset or budget["remaining"] <= 0:
                        break
                    budget["remaining"] -= 1
                    try:
                        total += await run(subset, depth + 1)
                    except ExpressionCopyTransientError as downstream:
                        raise ExpressionCopyTransientError(
                            kind=downstream.kind,
                            completed=total + downstream.completed,
                            retry_after=downstream.retry_after,
                        ) from downstream
                    except asyncio.CancelledError:
                        raise
                    except Exception as downstream:
                        if classify_llm_failure_kind(downstream) in {
                            "auth_failed",
                            "no_provider",
                        }:
                            prior = max(0, int(getattr(downstream, "completed", 0) or 0))
                            downstream.completed = total + prior  # type: ignore[attr-defined]
                        raise
                return total

        return await run(batch, 0)

    async def _precompute_single_fallback(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
    ) -> int:
        """Fallback: generate expressions one by one."""
        completed = 0
        for item in batch:
            generated = await self._try_generate_expression(item, profile)
            if generated is None:
                continue
            expression, topic_label = generated
            self._database.update_pool_copy(
                item.bvid,
                expression=expression,
                topic_label=topic_label,
            )
            item.pool_expression = expression
            item.pool_topic_label = topic_label
            completed += 1
        return completed

    async def generate_recommendations(
        self,
        discovered: list[DiscoveredContent] | None,
        profile: SoulProfile,
        limit: int = 10,
    ) -> list[Recommendation]:
        """Generate friend-style recommendations with real-time LLM expressions.

        Delegates to :meth:`serve` with ``expression_mode="realtime"``.
        The *discovered* parameter is accepted for backward compatibility but
        ignored — the engine always picks from the candidate pool.
        """
        return await self.serve(profile, limit=limit, expression_mode="realtime")

    async def reshuffle_recommendations(
        self,
        *,
        profile: SoulProfile,
        excluded_bvids: list[str] | None = None,
        limit: int = 5,
        source_platform: str = "",
    ) -> list[Recommendation]:
        """Instantly pick a new batch from the discovery pool.

        Delegates to :meth:`serve` with ``expression_mode="precomputed"``.
        ``source_platform`` narrows the batch to one canonical platform;
        empty keeps the historical cross-platform behaviour.
        """
        result = await self.reshuffle_recommendations_with_result(
            profile=profile,
            excluded_bvids=excluded_bvids,
            limit=limit,
            source_platform=source_platform,
        )
        return result.items

    async def reshuffle_recommendations_with_result(
        self,
        *,
        profile: SoulProfile,
        excluded_bvids: list[str] | None = None,
        limit: int = 5,
        source_platform: str = "",
    ) -> ServeResult:
        """Return a reshuffle batch with post-commit inventory metadata."""
        excluded = frozenset(
            bvid.strip() for bvid in (excluded_bvids or []) if bvid and bvid.strip()
        )
        return await self.serve_with_result(
            profile,
            limit=limit,
            excluded_bvids=excluded,
            expression_mode="precomputed",
            source_platform=source_platform,
        )

    async def append_recommendations(
        self,
        *,
        profile: SoulProfile,
        excluded_bvids: list[str],
        limit: int = 10,
        source_platform: str = "",
    ) -> list[Recommendation]:
        """Append another page of recommendations from the discovery pool.

        Delegates to :meth:`serve` with excluded BVIDs for pagination.
        """
        result = await self.append_recommendations_with_result(
            profile=profile,
            excluded_bvids=excluded_bvids,
            limit=limit,
            source_platform=source_platform,
        )
        return result.items

    async def append_recommendations_with_result(
        self,
        *,
        profile: SoulProfile,
        excluded_bvids: list[str],
        limit: int = 10,
        source_platform: str = "",
    ) -> ServeResult:
        """Return an appended page with post-commit inventory metadata."""
        excluded = frozenset(b.strip() for b in excluded_bvids if b and b.strip())
        return await self.serve_with_result(
            profile,
            limit=limit,
            excluded_bvids=excluded,
            expression_mode="precomputed",
            source_platform=source_platform,
        )

    async def generate_personal_topic(
        self,
        recommendations: list[Recommendation],
        profile: SoulProfile,
    ) -> PersonalTopic:
        """Create a deeply personalized recommendation topic.

        The topic is unique to this user — not "周末放松包" but something
        that connects to their specific personality and current state.

        Args:
            recommendations: Recommendations to group into a topic.
            profile: User's soul profile.

        Returns:
            A PersonalTopic with a custom title and description.
        """
        # TODO: Use LLM to create a personal topic narrative
        return PersonalTopic()

    async def generate_expression(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
    ) -> tuple[str, str]:
        """Generate a friend-style recommendation expression.

        The expression should feel like a close friend recommending something:
        warm, insightful, personal, with genuine understanding of why this
        specific person would enjoy this specific content.

        Args:
            content: The content being recommended.
            profile: User's soul profile.

        Returns:
            Expression text and a lightly personalized topic label.
        """
        generated = await self._try_generate_expression(content, profile)
        if generated is not None:
            return generated
        return self._fallback_expression(content), self._fallback_topic_label(profile)

    async def _try_generate_expression(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
    ) -> tuple[str, str] | None:
        """Try to generate personalized copy without applying a generic fallback."""
        from openbiliclaw.llm.prompts import build_recommendation_expression_prompt

        tone_profile = self._expression_tone_profile(profile, content)
        # Select most relevant interests for this content via embedding similarity
        interests_for_prompt = await self._select_relevant_interests(content, profile)

        profile_summary = _recommendation_profile_summary(
            profile,
            interests=interests_for_prompt,
        )
        messages = build_recommendation_expression_prompt(
            profile_summary=profile_summary,
            profile_blocks=self._profile_blocks(profile_summary, cache_key="expression"),
            content_summary={
                "title": content.title,
                "up_name": content.up_name,
                "description": content.description,
                "source_strategy": content.source_strategy,
                "style_key": normalize_style_key(content.style_key),
                "topic_group": content.topic_group,
                "relevance_score": content.relevance_score,
                "content_type": content.content_type,
                "body_text": content.body_text,
            },
            tone_profile=tone_profile,
            source_platform=content.source_platform or "bilibili",
        )
        try:
            complete_structured = self._llm.complete_structured_task
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                caller="recommendation.expression",
                **without_core_memory_kwargs(complete_structured),
            )
            payload = extract_llm_json_object(
                str(response.content),
                wrapper_keys=("result", "item", "expression", "data", "output"),
                item_predicate=lambda item: "expression" in item or "topic_label" in item,
            )
            if payload is None:
                raise ValueError("Expression response must be a JSON object.")
            validated = _validated_expression_fields(payload, content_key=content.bvid)
            if validated is not None:
                return validated
        except Exception:
            logger.exception("Failed to generate recommendation expression: %s", content.bvid)
        return None

    @staticmethod
    def _expression_tone_profile(
        profile: SoulProfile,
        content: DiscoveredContent,
    ) -> ToneProfile:
        tone = build_tone_profile(
            profile=profile,
            preference_summary={
                "style": _profile_style_summary(profile),
                "exploration_openness": profile.preferences.exploration_openness,
            },
            recent_feedback=[],
        )
        return tone

    def mark_presented(self, recommendation_ids: list[int]) -> None:
        """Mark recommendation rows as presented."""
        ids = [item for item in recommendation_ids if item > 0]
        if not ids:
            return
        self._database.mark_recommendations_presented(ids)

    async def record_feedback(
        self,
        recommendation_id: int,
        *,
        feedback_type: str,
        note: str = "",
    ) -> None:
        """Persist explicit user feedback for a recommendation."""
        self._database.update_recommendation_feedback(
            recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )

    def get_recommendation(self, recommendation_id: int) -> dict[str, object] | None:
        """Load a recommendation row for CLI or feedback workflows."""
        return self._database.get_recommendation_by_id(recommendation_id)

    @staticmethod
    def _ranking_key(
        item: DiscoveredContent,
        bonus: dict[str, float] | None = None,
    ) -> tuple[int, float, float, int, str]:
        # ``bonus`` (opt-in cover-visual, default empty) is added to the
        # relevance term only — tier priority and the timestamp/view/bvid
        # tiebreakers are untouched, so an empty map is byte-identical ranking.
        visual = (bonus or {}).get(item.bvid, 0.0)
        return (
            0 if item.candidate_tier == "primary" else 1,
            -(item.relevance_score + visual),
            -RecommendationEngine._timestamp_score(item.last_scored_at or item.discovered_at),
            -item.view_count,
            item.bvid,
        )

    @staticmethod
    def _timestamp_score(value: str) -> float:
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value.replace(" ", "T")).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _fallback_expression(content: DiscoveredContent) -> str:
        title = content.title or "这条内容"
        style_key = normalize_style_key(content.style_key)
        if style_key == "deep_focus":
            return f"《{title}》偏需要认真看进去，但会把结构和原理讲清楚。"
        if style_key == "quick_scan":
            return f"《{title}》适合快速抓重点，先把发生了什么和关键变化过一遍。"
        if style_key == "hands_on":
            return f"《{title}》偏能照着用的实操内容，不只是概念。"
        if style_key == "decision_support":
            return f"《{title}》适合用来做判断，能帮你快速比较重点和取舍。"
        if style_key == "story_immersion":
            return f"《{title}》更像进入一个故事，信息会跟着人物和事件一起展开。"
        if style_key == "opinion_sparring":
            return f"《{title}》偏观点碰撞，适合拿来校准一下自己的判断。"
        if style_key == "social_chat":
            return f"《{title}》胜在像有人把话讲开，适合随手点开听一会儿。"
        if style_key == "daily_wander":
            return f"《{title}》是低目标的生活流，看起来不费劲，氛围也顺。"
        if style_key == "mood_release":
            return f"《{title}》偏轻松释放，拿来换个脑子刚好。"
        if style_key == "aesthetic_browse":
            return f"《{title}》更偏审美浏览，适合先让画面和气质带你进去。"
        if style_key == "ambient_companion":
            return f"《{title}》适合当背景陪伴，不一定要一直盯着看。"
        if style_key == "live_pulse":
            return f"《{title}》偏现场和即时感，节奏会更直接。"
        if style_key == "curiosity_spark":
            return f"《{title}》胜在切口新鲜，适合点开看看这个陌生角度。"
        return f"《{title}》这条切口挺顺的，先丢给你看看，说不定正好能对上你当下的兴趣。"

    @staticmethod
    def _fallback_topic_label(profile: SoulProfile) -> str:
        if profile.core_traits:
            return f"你最近那股偏{profile.core_traits[0]}的状态"
        return "想先丢给你的一条"

    @staticmethod
    def _mmr_embedding_text(content: DiscoveredContent) -> str:
        """Canonical text shape for the MMR embedding cache key.

        Kept as a single source of truth so warm-time and serve-time
        agree on the cache key — otherwise the warm side fills L2 with
        one shape while serve() looks up a different one and never hits.
        """
        return (f"{content.title or ''} {(content.description or '')[:160]}").strip()[:200]

    async def _fetch_candidate_embeddings(
        self,
        candidates: list[DiscoveredContent],
    ) -> dict[str, list[float]]:
        """Cache-only embedding lookup for MMR diversification.

        **Never triggers a provider API call** — this is the hot path
        ``serve()`` runs on every "换一批" click and we contract a
        sub-second budget. Items missing from the cache simply fall
        through to the string-cap-only diversifier path; the warmer
        (``warm_mmr_embeddings`` from discovery / classify / refresh /
        startup) is responsible for filling the L2 SQLite cache so this
        lookup hits next time.

        Returns ``{bvid: vector}`` only for items already cached. Pure
        synchronous-via-async; no I/O.
        """
        if self._embedding_service is None or not candidates:
            return {}
        lookup = getattr(self._embedding_service, "lookup_cached", None)
        if not callable(lookup):
            return {}
        result: dict[str, list[float]] = {}
        for c in candidates:
            text = self._mmr_embedding_text(c)
            if not text:
                continue
            vec = lookup(text)
            if vec:
                result[c.bvid] = vec
        return result

    async def warm_mmr_embeddings(
        self,
        items: list[DiscoveredContent],
    ) -> int:
        """Pre-warm the embedding cache for items entering the pool.

        Called by discovery and pool-classification paths so the
        recommendation hot path (``serve`` → ``_fetch_candidate_embeddings``)
        is an L2 cache hit instead of a 30× sequential API round trip.
        Returns the number of items actually warmed (cache hits +
        successful API calls). Idempotent — ``EmbeddingService.embed``
        short-circuits on L1/L2 hit.
        """
        embedding_service = self._embedding_service
        if embedding_service is None or not items:
            return 0

        async def _warm(c: DiscoveredContent) -> bool:
            text = self._mmr_embedding_text(c)
            if not text:
                return False
            try:
                vec = await embedding_service.embed(text)
            except Exception:
                logger.debug(
                    "warm_mmr_embeddings: embed failed for %s",
                    c.bvid,
                    exc_info=True,
                )
                return False
            return bool(vec)

        results = await asyncio.gather(*(_warm(c) for c in items))
        return sum(1 for ok in results if ok)

    @classmethod
    async def _select_diversified_batch_async(
        cls,
        candidates: list[DiscoveredContent],
        *,
        limit: int,
        score_override: dict[str, float] | None = None,
        embeddings: dict[str, list[float]] | None = None,
        amplification_guard: set[str] | frozenset[str] | None = None,
        mmr_alpha: float = 0.5,
        mmr_beta: float = 0.5,
        relevance_bonus: dict[str, float] | None = None,
    ) -> list[DiscoveredContent]:
        """Run CPU-heavy ranking in a worker thread to preserve responsiveness."""
        result, _, _ = await cls._select_diversified_batch_with_timing_async(
            candidates,
            limit=limit,
            score_override=score_override,
            embeddings=embeddings,
            amplification_guard=amplification_guard,
            mmr_alpha=mmr_alpha,
            mmr_beta=mmr_beta,
            relevance_bonus=relevance_bonus,
        )
        return result

    @classmethod
    async def _select_diversified_batch_with_timing_async(
        cls,
        candidates: list[DiscoveredContent],
        *,
        limit: int,
        score_override: dict[str, float] | None = None,
        embeddings: dict[str, list[float]] | None = None,
        amplification_guard: set[str] | frozenset[str] | None = None,
        mmr_alpha: float = 0.5,
        mmr_beta: float = 0.5,
        relevance_bonus: dict[str, float] | None = None,
    ) -> tuple[list[DiscoveredContent], float, float]:
        """Return ranking plus worker CPU/wall and loop-resume delay timings."""

        def _select() -> tuple[list[DiscoveredContent], float, float]:
            worker_started = time.perf_counter()
            selected = cls._select_diversified_batch(
                candidates,
                limit=limit,
                score_override=score_override,
                embeddings=embeddings,
                amplification_guard=amplification_guard,
                mmr_alpha=mmr_alpha,
                mmr_beta=mmr_beta,
                relevance_bonus=relevance_bonus,
            )
            worker_finished = time.perf_counter()
            return selected, (worker_finished - worker_started) * 1000.0, worker_finished

        result, worker_ms, worker_finished = await asyncio.to_thread(_select)
        resume_delay_ms = max(0.0, (time.perf_counter() - worker_finished) * 1000.0)
        if worker_ms > 50.0 or resume_delay_ms > 50.0:
            logger.warning(
                "Recommendation diversity timing selector_worker_ms=%.1f "
                "event_loop_resume_delay_ms=%.1f candidates=%d",
                worker_ms,
                resume_delay_ms,
                len(candidates),
            )
        return result, worker_ms, resume_delay_ms

    @classmethod
    def _select_diversified_batch(
        cls,
        candidates: list[DiscoveredContent],
        *,
        limit: int,
        score_override: dict[str, float] | None = None,
        embeddings: dict[str, list[float]] | None = None,
        amplification_guard: set[str] | frozenset[str] | None = None,
        mmr_alpha: float = 0.5,
        mmr_beta: float = 0.5,
        relevance_bonus: dict[str, float] | None = None,
    ) -> list[DiscoveredContent]:
        bonus = relevance_bonus or {}
        if score_override:
            ranked = sorted(
                candidates,
                key=lambda item: -(score_override.get(item.bvid, 0.0) + bonus.get(item.bvid, 0.0)),
            )
        else:
            ranked = sorted(candidates, key=lambda item: cls._ranking_key(item, bonus))
        if limit <= 1 or len(ranked) <= 1:
            return ranked[:limit]

        # MMR path (v0.3.44+): when embeddings are available, replace the
        # simple relevance-ordered greedy selection with Maximum Marginal
        # Relevance — each pick balances "high relevance" against "low
        # similarity to already-picked items" via embedding cosine. This
        # catches "same topic, different LLM string label" duplication
        # that the topic_group / style_key string caps miss (e.g. three
        # rows tagged "人工智能" / "AI 趋势" / "AI 应用" that are
        # semantically the same content tier).
        if embeddings:
            return cls._select_with_mmr(
                ranked,
                limit=limit,
                score_override=score_override,
                embeddings=embeddings,
                amplification_guard=amplification_guard,
                alpha=mmr_alpha,
                beta=mmr_beta,
                relevance_bonus=relevance_bonus,
            )

        def _finalize(items: list[DiscoveredContent]) -> list[DiscoveredContent]:
            items = cls._ensure_accessible_entry(
                ranked=ranked,
                selected=items[:limit],
                limit=limit,
                score_override=score_override,
            )
            return cls._interleave_by_topic(items[:limit])

        per_topic_cap = cls._topic_cap(limit)
        soft_topic_cap = cls._soft_topic_cap(limit)
        per_style_cap = cls._style_cap(limit)
        broad_cap = cls._broad_topic_cap(limit)
        amplification_cap = cls._amplification_cap(limit)
        guard = cls._normalize_amplification_guard(amplification_guard)
        selected: list[DiscoveredContent] = []
        deferred: list[DiscoveredContent] = []
        topic_counts: dict[str, int] = {}
        broad_topic_counts: dict[str, int] = {}
        style_counts: dict[str, int] = {}
        amplification_counts: dict[str, int] = {}

        def _exceeds_broad_cap(item: DiscoveredContent) -> bool:
            bt = cls._broad_topic_token(item)
            return bool(bt) and broad_topic_counts.get(bt, 0) >= broad_cap

        def _track_broad(item: DiscoveredContent) -> None:
            bt = cls._broad_topic_token(item)
            if bt:
                broad_topic_counts[bt] = broad_topic_counts.get(bt, 0) + 1

        def _exceeds_amplification_cap(item: DiscoveredContent) -> bool:
            return any(
                amplification_counts.get(key, 0) >= amplification_cap
                for key in cls._candidate_amplification_keys(item) & guard
            )

        def _track_amplification(item: DiscoveredContent) -> None:
            for key in cls._candidate_amplification_keys(item) & guard:
                amplification_counts[key] = amplification_counts.get(key, 0) + 1

        for item in ranked:
            tokens = cls._diversity_tokens(item)
            style_token = cls._style_token(item)
            if _exceeds_amplification_cap(item):
                deferred.append(item)
                continue
            if tokens and any(topic_counts.get(token, 0) >= per_topic_cap for token in tokens):
                deferred.append(item)
                continue
            if _exceeds_broad_cap(item):
                deferred.append(item)
                continue
            if style_counts.get(style_token, 0) >= per_style_cap:
                deferred.append(item)
                continue
            selected.append(item)
            for token in tokens:
                topic_counts[token] = topic_counts.get(token, 0) + 1
            _track_broad(item)
            _track_amplification(item)
            style_counts[style_token] = style_counts.get(style_token, 0) + 1
            if len(selected) >= limit:
                return _finalize(selected)

        def try_fill(
            pool: list[DiscoveredContent],
            *,
            topic_cap: int,
            enforce_style_cap: bool,
            enforce_broad_cap: bool,
        ) -> list[DiscoveredContent]:
            remaining: list[DiscoveredContent] = []
            for item in pool:
                tokens = cls._diversity_tokens(item)
                style_token = cls._style_token(item)
                if _exceeds_amplification_cap(item):
                    remaining.append(item)
                    continue
                if tokens and any(topic_counts.get(token, 0) >= topic_cap for token in tokens):
                    remaining.append(item)
                    continue
                if enforce_broad_cap and _exceeds_broad_cap(item):
                    remaining.append(item)
                    continue
                if enforce_style_cap and style_counts.get(style_token, 0) >= per_style_cap:
                    remaining.append(item)
                    continue
                selected.append(item)
                for token in tokens:
                    topic_counts[token] = topic_counts.get(token, 0) + 1
                _track_broad(item)
                _track_amplification(item)
                style_counts[style_token] = style_counts.get(style_token, 0) + 1
                if len(selected) >= limit:
                    return []
            return remaining

        remaining = try_fill(
            deferred,
            topic_cap=per_topic_cap,
            enforce_style_cap=False,
            enforce_broad_cap=True,
        )
        if len(selected) < limit:
            remaining = try_fill(
                remaining,
                topic_cap=soft_topic_cap,
                enforce_style_cap=False,
                enforce_broad_cap=True,  # Never relax broad_cap
            )
        if len(selected) < limit:
            # Final fallback: topic diversity still holds at a relaxed
            # ceiling (2× the tight broad_cap). Topic is the true signal of
            # content richness — if 10 items share the same broad topic the
            # batch feels repetitive regardless of style or source. Items
            # with no topic (bt == "") are allowed through freely so we
            # still reach `limit` when the pool is thin but legitimate.
            fallback_broad_cap = broad_cap * 2
            for item in remaining:
                bt = cls._broad_topic_token(item)
                style_token = cls._style_token(item)
                if _exceeds_amplification_cap(item):
                    continue
                if bt and broad_topic_counts.get(bt, 0) >= fallback_broad_cap:
                    continue
                selected.append(item)
                if bt:
                    broad_topic_counts[bt] = broad_topic_counts.get(bt, 0) + 1
                _track_amplification(item)
                style_counts[style_token] = style_counts.get(style_token, 0) + 1
                if len(selected) >= limit:
                    break
        return _finalize(selected)

    @staticmethod
    def _amplification_cap(limit: int) -> int:
        import math

        return max(1, math.floor(limit * 0.25))

    @staticmethod
    def _normalize_amplification_guard(
        amplification_guard: set[str] | frozenset[str] | None,
    ) -> frozenset[str]:
        if not amplification_guard:
            return frozenset()
        from openbiliclaw.recommendation.curator import normalize_amplification_key

        return frozenset(
            key
            for key in (normalize_amplification_key(value) for value in amplification_guard)
            if key
        )

    @staticmethod
    def _candidate_amplification_keys(item: DiscoveredContent) -> set[str]:
        from openbiliclaw.recommendation.curator import candidate_amplification_keys

        return candidate_amplification_keys(item)

    @classmethod
    def _select_with_mmr(
        cls,
        ranked: list[DiscoveredContent],
        *,
        limit: int,
        score_override: dict[str, float] | None,
        embeddings: dict[str, list[float]],
        amplification_guard: set[str] | frozenset[str] | None,
        alpha: float,
        beta: float,
        relevance_bonus: dict[str, float] | None = None,
    ) -> list[DiscoveredContent]:
        """Greedy Maximum Marginal Relevance pick with existing string caps.

        At each step, choose the candidate maximising
        ``alpha * relevance - beta * max_cosine_to_picked``.

        ``alpha = beta = 0.5`` (default) gives a balanced relevance /
        diversity trade-off. Bumping ``beta`` up (or ``alpha`` down)
        produces a more aggressively varied batch at the cost of
        relevance. The string-based caps (``per_topic_cap`` /
        ``per_style_cap`` / ``broad_topic_cap``) still gate every
        pick — items violating them go to ``deferred`` and are only
        reconsidered if MMR ran out of compliant candidates.
        """
        from openbiliclaw.llm.embedding import cosine_similarity

        per_topic_cap = cls._topic_cap(limit)
        soft_topic_cap = cls._soft_topic_cap(limit)
        per_style_cap = cls._style_cap(limit)
        broad_cap = cls._broad_topic_cap(limit)
        amplification_cap = cls._amplification_cap(limit)
        guard = cls._normalize_amplification_guard(amplification_guard)
        topic_counts: dict[str, int] = {}
        broad_topic_counts: dict[str, int] = {}
        style_counts: dict[str, int] = {}
        amplification_counts: dict[str, int] = {}

        def _exceeds_broad_cap(item: DiscoveredContent) -> bool:
            bt = cls._broad_topic_token(item)
            return bool(bt) and broad_topic_counts.get(bt, 0) >= broad_cap

        def _track(item: DiscoveredContent) -> None:
            for token in cls._diversity_tokens(item):
                topic_counts[token] = topic_counts.get(token, 0) + 1
            bt = cls._broad_topic_token(item)
            if bt:
                broad_topic_counts[bt] = broad_topic_counts.get(bt, 0) + 1
            style_counts[cls._style_token(item)] = style_counts.get(cls._style_token(item), 0) + 1
            for key in cls._candidate_amplification_keys(item) & guard:
                amplification_counts[key] = amplification_counts.get(key, 0) + 1

        def _exceeds_amplification_cap(item: DiscoveredContent) -> bool:
            return any(
                amplification_counts.get(key, 0) >= amplification_cap
                for key in cls._candidate_amplification_keys(item) & guard
            )

        def _violates_caps(item: DiscoveredContent, *, topic_cap: int) -> bool:
            if _exceeds_amplification_cap(item):
                return True
            tokens = cls._diversity_tokens(item)
            if tokens and any(topic_counts.get(t, 0) >= topic_cap for t in tokens):
                return True
            if _exceeds_broad_cap(item):
                return True
            return style_counts.get(cls._style_token(item), 0) >= per_style_cap

        bonus = relevance_bonus or {}

        def _relevance(item: DiscoveredContent) -> float:
            base = (
                float(score_override.get(item.bvid, 0.0))
                if score_override
                else float(item.relevance_score or 0.0)
            )
            return base + bonus.get(item.bvid, 0.0)

        def _max_cos_to_picked(
            cand: DiscoveredContent,
            picked: list[DiscoveredContent],
        ) -> float:
            cand_vec = embeddings.get(cand.bvid)
            if not cand_vec or not picked:
                return 0.0
            best = 0.0
            for p in picked:
                p_vec = embeddings.get(p.bvid)
                if not p_vec:
                    continue
                sim = cosine_similarity(cand_vec, p_vec)
                if sim > best:
                    best = sim
            return best

        selected: list[DiscoveredContent] = []
        deferred: list[DiscoveredContent] = []
        remaining = list(ranked)

        # First pick: highest-relevance compliant item (MMR's "anchor"
        # — no penalty since picked is empty).
        # Subsequent picks: argmax(alpha*relevance - beta*max_cos_to_picked).
        while len(selected) < limit and remaining:
            best_idx = -1
            best_score = -1e9
            for idx, cand in enumerate(remaining):
                rel = _relevance(cand)
                penalty = _max_cos_to_picked(cand, selected)
                mmr = alpha * rel - beta * penalty
                if mmr > best_score:
                    best_score = mmr
                    best_idx = idx
            if best_idx < 0:
                break
            cand = remaining.pop(best_idx)
            if _violates_caps(cand, topic_cap=per_topic_cap):
                deferred.append(cand)
                continue
            selected.append(cand)
            _track(cand)

        # Re-fill from deferred if we ran out of compliant items —
        # progressively relax the topic cap, then drop style cap last,
        # mirroring the legacy fallback chain. broad_cap stays hard.
        if len(selected) < limit:
            still_deferred: list[DiscoveredContent] = []
            for cand in deferred:
                if len(selected) >= limit:
                    still_deferred.append(cand)
                    continue
                if _violates_caps(cand, topic_cap=soft_topic_cap):
                    still_deferred.append(cand)
                    continue
                selected.append(cand)
                _track(cand)
            deferred = still_deferred

        if len(selected) < limit:
            for cand in deferred:
                if len(selected) >= limit:
                    break
                # Final relaxation: only broad_cap still binding.
                if _exceeds_amplification_cap(cand):
                    continue
                if _exceeds_broad_cap(cand):
                    continue
                selected.append(cand)
                _track(cand)

        # Logging — surface MMR effect per call so we can tell if it
        # actually rotated the topic mix vs the relevance-only path.
        if selected:
            picked_topics = Counter(
                cls._normalize_topic_token(item.topic_group) or "unknown" for item in selected
            )
            top_share = picked_topics.most_common(1)[0][1] / len(selected)
            logger.debug(
                "MMR diversifier: picked %d/%d, alpha=%.2f beta=%.2f, "
                "unique_topics=%d top_topic_share=%.0f%%",
                len(selected),
                limit,
                alpha,
                beta,
                len(picked_topics),
                top_share * 100,
            )

        # Reuse legacy finalization (accessible_entry + interleave).
        finalized = cls._ensure_accessible_entry(
            ranked=ranked,
            selected=selected[:limit],
            limit=limit,
            score_override=score_override,
        )
        return cls._interleave_by_topic(finalized[:limit])

    @classmethod
    def _ensure_accessible_entry(
        cls,
        *,
        ranked: list[DiscoveredContent],
        selected: list[DiscoveredContent],
        limit: int,
        score_override: dict[str, float] | None,
    ) -> list[DiscoveredContent]:
        """Inject one easier-entry item when a full batch is uniformly hard.

        This only activates for full batches of 5+ items, and only when the
        pool already contains a reasonably competitive lighter-style option.
        """
        if limit < 5 or len(selected) < limit:
            return selected
        if any(cls._accessible_style_priority(item) > 0 for item in selected):
            return selected

        selected_ids = {item.bvid for item in selected}
        selected_topic_counts: Counter[str] = Counter()
        for item in selected:
            selected_topic_counts.update(cls._diversity_tokens(item))

        weakest_score = min(cls._effective_score(item, score_override) for item in selected)
        min_candidate_score = max(0.0, weakest_score - 0.10)

        candidates = [
            item
            for item in ranked
            if item.bvid not in selected_ids
            and cls._accessible_style_priority(item) > 0
            and cls._effective_score(item, score_override) >= min_candidate_score
        ]
        candidates.sort(
            key=lambda item: (
                -cls._accessible_style_priority(item),
                -cls._effective_score(item, score_override),
                cls._ranking_key(item),
            ),
        )

        topic_cap = cls._topic_cap(limit)
        for candidate in candidates:
            candidate_tokens = cls._diversity_tokens(candidate)
            replacement_idx: int | None = None
            for idx in range(len(selected) - 1, -1, -1):
                current = selected[idx]
                if cls._accessible_style_priority(current) > 0:
                    continue
                remaining_topics = Counter(selected_topic_counts)
                remaining_topics.subtract(cls._diversity_tokens(current))
                if candidate_tokens and any(
                    remaining_topics.get(token, 0) >= topic_cap for token in candidate_tokens
                ):
                    continue
                replacement_idx = idx
                break
            if replacement_idx is not None:
                swapped = list(selected)
                swapped[replacement_idx] = candidate
                return swapped
        return selected

    @staticmethod
    def _effective_score(
        item: DiscoveredContent,
        score_override: dict[str, float] | None,
    ) -> float:
        if score_override is None:
            return item.relevance_score
        return score_override.get(item.bvid, item.relevance_score)

    @staticmethod
    def _accessible_style_priority(item: DiscoveredContent) -> int:
        style_key = RecommendationEngine._style_token(item)
        if style_key in {"ambient_companion", "daily_wander", "mood_release"}:
            return 7
        if style_key in {"social_chat", "aesthetic_browse", "live_pulse"}:
            return 6
        if style_key in {"curiosity_spark", "decision_support"}:
            return 4
        if style_key in {"story_immersion", "opinion_sparring"}:
            return 3
        if style_key in {"quick_scan", "hands_on"}:
            return 2
        if style_key == "deep_focus":
            return 1
        return 0

    @staticmethod
    def _diversity_tokens(item: DiscoveredContent) -> set[str]:
        """Use topic_group (coarse semantic category) for diversity bucketing."""
        topic_group = RecommendationEngine._normalize_topic_token(item.topic_group)
        if topic_group:
            return {topic_group}

        topic_key = RecommendationEngine._normalize_topic_token(item.topic_key)
        if topic_key:
            return {topic_key}

        tokens = {
            RecommendationEngine._normalize_topic_token(tag)
            for tag in item.tags
            if RecommendationEngine._normalize_topic_token(tag)
        }
        if tokens:
            return tokens

        # Fallback: use author + title keywords as diversity signals.
        # NOTE: source_strategy is intentionally excluded — when many items
        # share the same source_strategy (e.g. "xhs-extension-task"), using
        # it as a topic token makes the diversity mechanism treat them as
        # "same topic" and collapse the entire batch into one bucket.
        fallback_fields = [item.up_name]
        title = item.title
        fallback_fields.extend(re.findall(r"[A-Za-z0-9]{2,}", title))
        # Also extract Chinese character runs from the title as fallback
        # topic signals — these are far more discriminating than
        # source_strategy for content that lacks proper classification.
        fallback_fields.extend(m for m in re.findall(r"[\u4e00-\u9fff]{2,4}", title))
        return {
            RecommendationEngine._normalize_topic_token(value)
            for value in fallback_fields
            if RecommendationEngine._normalize_topic_token(value)
        }

    @staticmethod
    def _style_token(item: DiscoveredContent) -> str:
        """Normalize style_key into a cap-tracked bucket.

        Empty/missing style_key maps to the sentinel ``"unknown"`` so that
        unclassified content (common for xhs notes, which lack the bilibili
        style classification) still participates in the per-style cap.
        Without this, unclassified items would all bypass style_counts and
        could flood a batch with visually monotonous rows.
        """
        token = RecommendationEngine._normalize_topic_token(normalize_style_key(item.style_key))
        return token or "unknown"

    @staticmethod
    def _broad_topic_token(item: DiscoveredContent) -> str:
        """Extract a broad topic category for cross-variant grouping.

        Uses topic_group directly when available (already coarse).
        Falls back to first 4 chars of topic_key for legacy data.
        """
        group = RecommendationEngine._normalize_topic_token(item.topic_group)
        if group:
            return group
        raw = RecommendationEngine._normalize_topic_token(item.topic_key)
        if not raw:
            return ""
        if raw.startswith("related:"):
            return "related"
        return raw[:4]

    @staticmethod
    def _broad_topic_cap(limit: int) -> int:
        """Maximum items sharing the same broad topic category."""
        if limit <= 5:
            return 2
        if limit <= 10:
            return 3
        return 4

    @classmethod
    def _interleave_by_topic(
        cls,
        items: list[DiscoveredContent],
    ) -> list[DiscoveredContent]:
        """Reorder items so same-topic content is maximally spread apart.

        Uses round-robin from groups sorted by size (largest first).
        """
        if len(items) <= 2:
            return items
        groups: dict[str, list[DiscoveredContent]] = {}
        for item in items:
            key = cls._broad_topic_token(item) or item.bvid
            groups.setdefault(key, []).append(item)
        buckets = sorted(groups.values(), key=len, reverse=True)
        result: list[DiscoveredContent] = []
        while buckets:
            for bucket in buckets:
                if bucket:
                    result.append(bucket.pop(0))
            buckets = [b for b in buckets if b]
        return result

    @staticmethod
    def _normalize_topic_token(value: str) -> str:
        text = value.strip().lower()
        if not text:
            return ""
        compact = re.sub(r"\s+", "", text)
        return compact[:24]

    @staticmethod
    def _topic_cap(limit: int) -> int:
        return 1 if limit <= 5 else 2

    @staticmethod
    def _soft_topic_cap(limit: int) -> int:
        return 2 if limit <= 5 else 3

    @staticmethod
    def _style_cap(limit: int) -> int:
        return max(1, min(3, (limit + 1) // 3))

    @staticmethod
    def _platform_token(item: DiscoveredContent) -> str:
        """Platform label for observability only — not used to filter picks.

        Diversity and caps are driven by content features (topic and style).
        Exposed in ``_build_debug_summary`` so log readers can still see the
        platform split per round.
        """
        platform = (item.source_platform or "").strip().lower()
        return platform or "bilibili"

    def _rows_to_discovered(
        self,
        rows: list[dict[str, Any]],
    ) -> list[DiscoveredContent]:
        """Map raw DB pool rows into ``DiscoveredContent`` dataclasses.

        Single source of truth for the row → dataclass field mapping so
        adding/removing a pool column only needs one edit.
        """
        from openbiliclaw.discovery.engine import DiscoveredContent

        return [
            DiscoveredContent(
                bvid=str(row.get("bvid", "")),
                title=str(row.get("title", "")),
                up_name=str(row.get("up_name", "")),
                up_mid=int(row.get("up_mid", 0) or 0),
                duration=int(row.get("duration", 0) or 0),
                description=str(row.get("description", "")),
                published_at=str(row.get("published_at", "") or ""),
                published_label=str(row.get("published_label", "") or ""),
                cover_url=str(row.get("cover_url", "")),
                view_count=int(row.get("view_count", 0) or 0),
                like_count=int(row.get("like_count", 0) or 0),
                favorite_count=int(row.get("favorite_count", 0) or 0),
                collect_count=int(row.get("collect_count", 0) or 0),
                comment_count=int(row.get("comment_count", 0) or 0),
                share_count=int(row.get("share_count", 0) or 0),
                danmaku_count=int(row.get("danmaku_count", 0) or 0),
                reply_count=int(row.get("reply_count", 0) or 0),
                retweet_count=int(row.get("retweet_count", 0) or 0),
                bookmark_count=int(row.get("bookmark_count", 0) or 0),
                author_name=str(row.get("author_name", "") or ""),
                tags=self._parse_tags(row.get("tags", "[]")),
                topic_key=str(row.get("topic_key", "")),
                topic_group=str(row.get("topic_group", "")),
                style_key=str(row.get("style_key", "")),
                source_strategy=str(row.get("source", "")),
                relevance_score=float(row.get("relevance_score", 0.0) or 0.0),
                relevance_reason=str(row.get("relevance_reason", "")),
                pool_expression=str(row.get("pool_expression", "")),
                pool_topic_label=str(row.get("pool_topic_label", "")),
                candidate_tier=str(row.get("candidate_tier", "primary") or "primary"),
                discovered_at=str(row.get("discovered_at", "")),
                last_scored_at=str(row.get("last_scored_at", "")),
                content_id=str(row.get("content_id", "") or row.get("bvid", "")),
                content_url=str(row.get("content_url", "")),
                source_platform=str(row.get("source_platform", "") or "bilibili"),
                content_type=str(row.get("content_type", "") or "video"),
                body_text=str(row.get("body_text", "") or ""),
            )
            for row in rows
        ]

    def _load_pool_candidates(self, *, limit: int) -> list[DiscoveredContent]:
        rows = self._database.get_pool_candidates(
            limit=limit, xhs_self_nickname=self._xhs_self_nickname()
        )
        return self._rows_to_discovered(rows)

    def _load_pool_candidates_for_platform(
        self,
        platform: str,
        *,
        limit: int,
    ) -> list[DiscoveredContent]:
        """Load one platform's servable pool rows on the compatibility path.

        Returns nothing when the adapter predates platform-scoped reads —
        an empty scoped batch is the safe outcome, whereas falling back to
        the cross-platform window would answer a scoped request with other
        platforms' content.
        """
        fetch = getattr(self._database, "get_pool_candidates_for_platform", None)
        if not callable(fetch):
            logger.warning(
                "Database adapter %s cannot serve platform-scoped requests "
                "(no get_pool_candidates_for_platform); returning an empty batch.",
                type(self._database).__name__,
            )
            return []
        rows = fetch(platform, limit, xhs_self_nickname=self._xhs_self_nickname())
        return self._rows_to_discovered(list(rows))

    def _load_filtered_serve_candidates(
        self,
        profile: SoulProfile,
        *,
        limit: int,
        excluded_bvids: frozenset[str],
        source_platform: str = "",
    ) -> tuple[list[DiscoveredContent], int, int, int, int]:
        """Load and filter one serve window outside the asyncio event loop."""
        scope = normalize_source_platform(source_platform)
        if scope:
            candidates = self._enforce_platform_scope(
                self._load_pool_candidates_for_platform(scope, limit=limit),
                scope,
            )
            loaded_count = len(candidates)
        else:
            candidates = self._load_pool_candidates(limit=limit)
            loaded_count = len(candidates)
            # The platform floor exists to rescue platforms a cross-platform
            # relevance window drops; under an explicit scope it would be the
            # very cross-platform leak the scope forbids.
            candidates = self._apply_platform_floor(candidates)
        if excluded_bvids:
            candidates = [item for item in candidates if item.bvid not in excluded_bvids]
        after_exclude_count = len(candidates)
        candidates = self._exclude_disliked_topic_candidates(candidates, profile)
        after_disliked_count = len(candidates)
        candidates = self._exclude_recently_viewed(candidates)
        return (
            candidates,
            loaded_count,
            after_exclude_count,
            after_disliked_count,
            len(candidates),
        )

    def _score_candidates_with_curator(
        self,
        candidates: list[DiscoveredContent],
        curator_snapshot: tuple[
            list[dict[str, object]],
            list[dict[str, object]],
        ]
        | None = None,
    ) -> tuple[dict[str, float] | None, frozenset[str]]:
        """Build curator context and scores outside the asyncio event loop."""
        if self._curator is None:
            return None, frozenset()
        build_from_rows = getattr(self._curator, "build_context_from_rows", None)
        if curator_snapshot is not None and callable(build_from_rows):
            context = build_from_rows(*curator_snapshot)
        else:
            context = self._curator.build_context()
        return (
            self._curator.score_candidates(candidates, context),
            context.over_budget_amplification_keys,
        )

    def _apply_platform_floor(self, candidates: list[DiscoveredContent]) -> list[DiscoveredContent]:
        """Guarantee every stocked platform is represented in the serve window.

        A single relevance-ordered window can be 100% one platform (e.g. all
        bilibili early in a session) even while zhihu/xhs/douyin rows sit
        servable in the pool, leaving those tabs empty for hours. For each
        servable platform missing from the window, pull up to 5 rows and append
        them (dedup by bvid). The downstream MMR/diversifier is unchanged — it
        just can no longer silently drop a stocked platform. Skipped entirely
        for single-platform pools (common bilibili-only installs).
        """
        list_platforms = getattr(self._database, "list_servable_pool_platforms", None)
        fetch_for_platform = getattr(self._database, "get_pool_candidates_for_platform", None)
        if not callable(list_platforms) or not callable(fetch_for_platform):
            return candidates
        nickname = self._xhs_self_nickname()
        try:
            servable_platforms = [
                str(token).strip().lower()
                for token in list_platforms(xhs_self_nickname=nickname)
                if str(token).strip()
            ]
        except Exception:
            logger.debug("platform floor: servable platform lookup failed", exc_info=True)
            return candidates
        # Single-platform pools never need a floor — zero behavior change there.
        if len(servable_platforms) <= 1:
            return candidates

        present = {self._platform_token(item) for item in candidates}
        missing = [token for token in servable_platforms if token not in present]
        if not missing:
            return candidates

        seen_bvids = {item.bvid for item in candidates if item.bvid}
        topped_up: list[tuple[str, int]] = []
        for platform in missing:
            try:
                rows = fetch_for_platform(platform, limit=5, xhs_self_nickname=nickname)
            except Exception:
                logger.debug("platform floor: fetch failed for %s", platform, exc_info=True)
                continue
            added = 0
            for item in self._rows_to_discovered(rows):
                if item.bvid and item.bvid in seen_bvids:
                    continue
                if item.bvid:
                    seen_bvids.add(item.bvid)
                candidates.append(item)
                added += 1
            if added:
                topped_up.append((platform, added))
        if topped_up:
            logger.info(
                "serve platform floor topped up %s",
                ", ".join(f"{name}+{count}" for name, count in topped_up),
            )
        return candidates

    def _load_pool_candidates_needing_copy(self, *, limit: int) -> list[DiscoveredContent]:
        rows = self._database.get_pool_candidates_needing_copy(
            limit=limit, xhs_self_nickname=self._xhs_self_nickname()
        )
        return self._rows_to_discovered(rows)

    def _exclude_recently_viewed(
        self,
        candidates: list[DiscoveredContent],
    ) -> list[DiscoveredContent]:
        get_seen = getattr(self._database, "get_seen_bvids", None)
        if not callable(get_seen):
            get_seen = self._database.get_recent_viewed_bvids
        viewed_bvids = get_seen()
        if not viewed_bvids:
            return candidates
        return [item for item in candidates if item.bvid not in viewed_bvids]

    @classmethod
    def _exclude_disliked_topic_candidates(
        cls,
        candidates: list[DiscoveredContent],
        profile: SoulProfile,
    ) -> list[DiscoveredContent]:
        terms = cls._normalized_disliked_topics(profile)
        if not terms:
            return candidates
        return [item for item in candidates if not cls._matches_disliked_topic(item, terms)]

    @classmethod
    def _normalized_disliked_topics(cls, profile: SoulProfile) -> list[str]:
        raw_topics = getattr(getattr(profile, "preferences", None), "disliked_topics", []) or []
        result: list[str] = []
        seen: set[str] = set()
        for topic in raw_topics:
            term = cls._normalize_dislike_match_text(topic)
            if len(term) < 2 or term in seen:
                continue
            seen.add(term)
            result.append(term)
        return result

    @classmethod
    def _matches_disliked_topic(
        cls,
        item: DiscoveredContent,
        disliked_terms: list[str],
    ) -> bool:
        exact_fields = [
            cls._normalize_dislike_match_text(item.topic_key),
            cls._normalize_dislike_match_text(item.topic_group),
            cls._normalize_dislike_match_text(item.pool_topic_label),
        ]
        search_fields = [
            cls._normalize_dislike_match_text(item.title),
            cls._normalize_dislike_match_text(item.pool_topic_label),
            cls._normalize_dislike_match_text(item.description),
            cls._normalize_dislike_match_text(item.up_name),
            cls._normalize_dislike_match_text((item.body_text or "")[:800]),
            *[cls._normalize_dislike_match_text(tag) for tag in item.tags],
        ]
        for term in disliked_terms:
            if term in exact_fields:
                return True
            if any(term in field for field in search_fields if field):
                return True
        return False

    @staticmethod
    def _normalize_dislike_match_text(value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"\s+", "", text)

    @staticmethod
    def _parse_tags(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [str(item).strip() for item in payload if str(item).strip()]

    @classmethod
    def _build_debug_summary(
        cls,
        candidates: list[DiscoveredContent],
        *,
        prev_bvids: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Build a content-diversity-focused debug payload for one batch.

        v0.3.31+: enriched to surface what really matters for "is this
        batch diverse" diagnosis:

        - ``unique_topics`` / ``unique_franchises``: total distinct
          values, not just top-5. The previous summary's top-5 hid
          tail diversity.
        - ``top_topic_share`` / ``top_style_share`` /
          ``top_franchise_share``: dominance ratio (max-bucket-count /
          total). >0.4 on any of these = "this batch's content is
          concentrated", <0.2 = "well-spread".
        - ``carryover_from_prev``: how many items in this batch also
          showed in the previous batch (when ``prev_bvids`` is given).
          Tells you if the recommender keeps re-serving the same content.
        - ``unique_titles_ratio``: distinct titles / count. <1.0 means
          the same title appears multiple times in one batch (data quality
          issue; same content cross-source).
        """
        n = len(candidates)
        if n == 0:
            return {"count": 0}

        style_counts = Counter(cls._style_token(item) or "unknown" for item in candidates)
        source_counts = Counter(
            cls._normalize_topic_token(item.source_strategy) or "unknown" for item in candidates
        )
        platform_counts = Counter(cls._platform_token(item) for item in candidates)

        # Topic group counts. v0.3.46+: when an item has no proper
        # ``topic_group`` / ``topic_key`` / tags (i.e. classify_pool_backlog
        # hasn't run yet), bucket it as ``"_unclassified_"`` rather than
        # leaning on ``_diversity_tokens()``'s title-prefix fallback —
        # otherwise the summary log would print fake-looking topics like
        # ``"165"``, ``"屎屎"`` or ``"三花"`` extracted from raw titles
        # before the LLM evaluator gets to assign a real category.
        # The bucketing path (used by the actual diversifier) keeps the
        # fallback so unclassified items don't all collapse into one
        # bucket — but the summary should not lie about what's there.
        topic_counts: Counter[str] = Counter()
        for item in candidates:
            primary = cls._normalize_topic_token(item.topic_group) or cls._normalize_topic_token(
                item.topic_key
            )
            if primary:
                topic_counts[primary] += 1
                continue
            tag_tokens = {
                cls._normalize_topic_token(tag)
                for tag in item.tags
                if cls._normalize_topic_token(tag)
            }
            if tag_tokens:
                topic_counts[sorted(tag_tokens)[0]] += 1
            else:
                topic_counts["_unclassified_"] += 1

        # Franchise key — exclude empty (non-IP-bearing content). This
        # is OUR guard against "5 different 原神 angle videos in one
        # batch" (same franchise, different topic_group).
        franchise_counts: Counter[str] = Counter(
            (getattr(item, "franchise_key", "") or "").strip().lower() for item in candidates
        )
        del franchise_counts[""]  # don't count non-franchise content

        # Carryover with previous batch — biggest "stale recommendations"
        # signal users complain about. Stored on the engine across calls.
        carryover = 0
        if prev_bvids is not None:
            carryover = sum(1 for item in candidates if item.bvid in prev_bvids)

        unique_titles = len({item.title.strip() for item in candidates if item.title})

        def _share(counts: Counter[str]) -> float:
            if not counts:
                return 0.0
            return round(counts.most_common(1)[0][1] / n, 3)

        return {
            "count": n,
            "platforms": dict(platform_counts.most_common()),
            "styles": dict(style_counts.most_common(5)),
            "sources": dict(source_counts.most_common(5)),
            "topics": dict(topic_counts.most_common(5)),
            # New v0.3.31 content-diversity fields
            "unique_topics": len(topic_counts),
            "unique_franchises": len(franchise_counts),
            "top_topic_share": _share(topic_counts),
            "top_style_share": _share(style_counts),
            "top_franchise_share": _share(franchise_counts),
            "top_franchise": (franchise_counts.most_common(1)[0][0] if franchise_counts else ""),
            "carryover_from_prev": carryover,
            "unique_titles_ratio": round(unique_titles / n, 3),
            "sample_titles": [item.title for item in candidates[:5]],
        }
