"""Recommendation Engine — ranking, expression, and delivery.

Handles the final stage: taking discovered content and presenting it
to the user in a warm, friend-like manner with deep personal insights.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from openbiliclaw.llm.json_utils import extract_llm_json_list, extract_llm_json_object
from openbiliclaw.llm.service import is_llm_rate_limit_error
from openbiliclaw.soul.tone import ToneProfile, build_tone_profile

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.recommendation.curator import PoolCurator
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


def _profile_style_summary(profile: SoulProfile) -> dict[str, object]:
    style = profile.preferences.style
    return {
        "preferred_duration": style.preferred_duration,
        "preferred_pace": style.preferred_pace,
        "humor_preference": style.humor_preference,
        "depth_preference": style.depth_preference,
    }


def _profile_context_summary(profile: SoulProfile) -> dict[str, object]:
    context = profile.preferences.context
    return {
        "weekday_patterns": context.weekday_patterns,
        "weekend_patterns": context.weekend_patterns,
        "time_of_day_patterns": context.time_of_day_patterns,
        "session_type": context.session_type,
    }


def _clone_tone_profile(tone: ToneProfile) -> ToneProfile:
    return {
        "density": tone["density"],
        "warmth": tone["warmth"],
        "playfulness": tone["playfulness"],
        "directness": tone["directness"],
    }


def _recommendation_profile_summary(
    profile: SoulProfile,
    *,
    interests: list[dict[str, object]] | None = None,
    include_active_insights: bool = False,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "personality_portrait": profile.personality_portrait,
        "core_traits": profile.core_traits[:5],
        "deep_needs": profile.deep_needs[:5],
        "interests": interests
        if interests is not None
        else [
            {
                "name": item.name,
                "category": item.category,
                "weight": item.weight,
            }
            for item in profile.preferences.interests[:10]
        ],
        "style": _profile_style_summary(profile),
        "context": _profile_context_summary(profile),
        "exploration_openness": profile.preferences.exploration_openness,
        "disliked_topics": profile.preferences.disliked_topics[:5],
    }
    if include_active_insights:
        summary["active_insights"] = [
            {
                "hypothesis": str(getattr(ins, "hypothesis", "")),
                "confidence": float(getattr(ins, "confidence", 0.5)),
            }
            for ins in getattr(profile, "active_insights", [])[:5]
        ]
    return summary


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
    saw_identifier = False
    for item in payload:
        raw_key = str(item.get("bvid") or item.get("content_id") or "").strip()
        if not raw_key:
            continue
        saw_identifier = True
        if raw_key not in valid_keys:
            continue
        matched[raw_key] = item

    return matched if saw_identifier else None


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
    ) -> None:
        self._llm = llm
        self._database = database
        self._curator = curator
        self._embedding_service = embedding_service
        self._xhs_self_info_provider = xhs_self_info_provider
        # v0.3.63+: optional registry for detached fire-and-forget tasks
        # (classify_pool_backlog_detached, precompute_delight_scores_detached).
        # When provided, those tasks register here so RuntimeContext's
        # hot-reload can cancel them before the new runtime starts.
        # When None, the engine falls back to bare asyncio.create_task —
        # tests that don't inject a registry continue to work unchanged.
        self.task_registry: BackgroundTaskRegistry | None = task_registry
        self._classify_lock = asyncio.Lock()
        # v0.3.47+: serialise precompute_pool_copy so multiple
        # per-strategy fire-and-forget tasks (now created from
        # _run_refresh_plan after each strategy completes) don't load
        # the same un-precomputed candidates and double-spend LLM tokens.
        #
        # v0.3.62+: split the previous single ``_precompute_lock`` into
        # two independent locks. The old shared lock serialised
        # expression generation and delight scoring — when delight
        # scoring was slow (LLM backoff or a large un-scored backlog),
        # the next expression batch had to wait behind it even though
        # nothing about expression touches delight state. Now expression
        # generation holds ``_expression_lock`` while delight scoring
        # runs in a detached task guarded by ``_delight_lock``, so the
        # two flows progress independently and back-to-back precompute
        # calls still avoid double-spending delight LLM tokens.
        self._expression_lock = asyncio.Lock()
        self._delight_lock = asyncio.Lock()
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
    ) -> list[Recommendation]:
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
            List of personalized recommendations.
        """
        label = "realtime" if expression_mode == "realtime" else "pool"
        multiplier = 4 if excluded_bvids else 3
        pool_readiness = self._pool_readiness_counts()
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
            return []

        candidates = self._load_pool_candidates(limit=max(limit * multiplier, 40))
        loaded_count = len(candidates)
        if excluded_bvids:
            candidates = [c for c in candidates if c.bvid not in excluded_bvids]
        after_exclude_count = len(candidates)
        candidates = self._exclude_recently_viewed(candidates)
        after_viewed_count = len(candidates)
        if after_viewed_count == 0:
            logger.warning(
                "serve(/%s) loaded 0 usable candidates from servable=%d "
                "(raw=%d pending=%d) after filters: loaded=%d "
                "after_exclude=%d after_viewed=%d. Skipping curator, "
                "MMR embeddings, and recommendation writes.",
                label,
                servable_pool_count,
                raw_pool_count,
                pending_pool_count,
                loaded_count,
                after_exclude_count,
                after_viewed_count,
            )
            self._last_served_bvids = frozenset()
            return []

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
                " → after_exclude=%d → after_viewed=%d (raw=%d pending=%d)",
                label,
                servable_pool_count,
                loaded_count,
                after_exclude_count,
                after_viewed_count,
                raw_pool_count,
                pending_pool_count,
            )

        logger.info(
            "Recommendation candidate summary (serve/%s): %s",
            label,
            json.dumps(
                self._build_debug_summary(candidates, prev_bvids=prev_bvids),
                ensure_ascii=False,
            ),
        )

        score_override: dict[str, float] | None = None
        amplification_guard: frozenset[str] = frozenset()
        if self._curator is not None:
            context = self._curator.build_context()
            score_override = self._curator.score_candidates(candidates, context)
            amplification_guard = context.over_budget_amplification_keys

        # v0.3.44+: pre-fetch embeddings for MMR-based diversification.
        # In v0.3.45+ discovery and classify_pool_backlog warm these into
        # the L2 SQLite cache up front, so this should be near-zero on
        # the hot path. The elapsed/coverage log below makes regressions
        # in cache warming visible — sustained "elapsed > 500ms" or
        # "coverage < 100%" means warm hooks are missing items.
        import time as _time

        _embed_t0 = _time.monotonic()
        embeddings = await self._fetch_candidate_embeddings(candidates)
        _embed_elapsed_ms = (_time.monotonic() - _embed_t0) * 1000.0
        if candidates:
            logger.info(
                "MMR embedding fetch: coverage=%d/%d elapsed=%.0fms",
                len(embeddings),
                len(candidates),
                _embed_elapsed_ms,
            )

        ranked = self._select_diversified_batch(
            candidates,
            limit=limit,
            score_override=score_override,
            embeddings=embeddings,
            amplification_guard=amplification_guard,
        )
        logger.info(
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

        # Critical-path write: only the insert (we need the IDs for the
        # response). Single transaction, single fsync.
        ids = self._database.batch_insert_recommendations(
            [
                {
                    "bvid": rec.content.bvid,
                    "expression": rec.expression,
                    "topic": rec.topic_label,
                    "confidence": rec.confidence,
                    "presented": 0,
                }
                for rec in recommendations
            ]
        )
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

        # v0.3.45+: detach pool_status='shown' update from the response
        # critical path. Under refresh-tick write contention (eg.
        # _enforce_pool_cap reactivating 300+ rows) this UPDATE could
        # wait 0.5-1.5s for the SQLite write lock, blowing the <1s
        # budget. Within-session double-click protection is already
        # provided by `_last_served_bvids` (in-memory) so it's safe to
        # let the persistent flag commit slightly later.
        ranked_bvids = [item.bvid for item in ranked]
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._mark_pool_shown_async(ranked_bvids))
        except RuntimeError:
            # serve() is normally invoked from an event loop; only the
            # rare sync-test path falls through here.
            self._database.mark_pool_items_shown(ranked_bvids)
        return recommendations

    async def _mark_pool_shown_async(self, bvids: list[str]) -> None:
        """Fire-and-forget pool-marking helper. Never raises."""
        try:
            self._database.mark_pool_items_shown(bvids)
        except Exception:
            logger.exception(
                "mark_pool_items_shown (detached) failed for %d bvids",
                len(bvids),
            )

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
        all_interests = [
            {"name": item.name, "category": item.category, "weight": item.weight}
            for item in profile.preferences.interests[:15]
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

        from openbiliclaw.llm.embedding import cosine_similarity

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

        # Union-find on the embeddings to derive canonical labels
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

        strict = self._SUPERGROUP_STRICT_THRESHOLD
        loose = self._SUPERGROUP_LOOSE_THRESHOLD
        prefix_len = self._SUPERGROUP_PREFIX_LEN
        for i, ga in enumerate(labels):
            for gb in labels[i + 1 :]:
                sim = cosine_similarity(embeddings[ga], embeddings[gb])
                shared_prefix = ga[:prefix_len] == gb[:prefix_len] and len(ga) >= prefix_len
                if sim >= strict or (shared_prefix and sim >= loose):
                    union(ga, gb)

        new_map: dict[str, str] = {}
        for label in labels:
            canonical = find(label)
            if canonical != label:
                new_map[label] = canonical
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
        """
        if self._embedding_service is None:
            return 0
        candidates = self._load_pool_candidates(limit=limit)
        if not candidates:
            return 0
        warmed = await self.warm_mmr_embeddings(candidates)
        logger.info(
            "Pool MMR embedding prewarm: %d/%d items warmed",
            warmed,
            len(candidates),
        )
        return warmed

    async def precompute_pool_copy(
        self,
        *,
        profile: SoulProfile,
        limit: int = 20,
        delight_limit: int = 30,
        batch_size: int = 30,
    ) -> int:
        """Precompute fast-path popup copy for fresh pool candidates.

        v0.3.47+: batches dispatched in parallel via ``asyncio.gather``,
        and ``batch_size`` defaults to 30 (matches discovery's eval batch).
        With the previous serial × ``batch_size=8`` shape, a 60-item
        backlog needed 8 LLM calls and 8 sequential round trips. The new
        shape needs 2 LLM calls running concurrently — popup copy
        catches up minutes faster.

        v0.3.62+: expression generation is guarded by
        ``self._expression_lock``; delight scoring runs in a detached
        ``asyncio.create_task`` with its own ``self._delight_lock``. The
        previous single ``_precompute_lock`` held both flows under one
        gate, so a slow delight pass would stall the next expression
        batch even though pool items already needed ``pool_expression``.
        Splitting the locks lets expression and delight progress
        independently while the per-flow lock still prevents
        back-to-back fires from double-spending LLM tokens on the same
        items.

        The per-strategy fire-and-forget tasks queued from
        ``_run_refresh_plan`` therefore can't load the same
        un-precomputed candidates twice for expression generation.

        Also runs delight scoring on un-scored candidates and generates
        delight reasons for items above the delight threshold.

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

        async with self._expression_lock:
            candidates = self._load_pool_candidates_needing_copy(limit=max(0, limit))
            if not candidates:
                _spawn_delight()
                return 0

            batches = [
                candidates[i : i + batch_size] for i in range(0, len(candidates), batch_size)
            ]
            results = await asyncio.gather(
                *(self._precompute_batch(batch, profile) for batch in batches),
                return_exceptions=True,
            )
            completed = 0
            for r in results:
                if isinstance(r, BaseException):
                    logger.warning("Expression batch failed: %s", r)
                    continue
                completed += int(r or 0)

        # Fire delight scoring outside the expression lock so the next
        # expression batch can start immediately while delight catches up.
        _spawn_delight()
        return completed

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
        """
        try:
            return await self.classify_pool_backlog(profile=profile, limit=limit)
        except Exception:
            logger.exception("classify_pool_backlog (detached) failed")
            return 0

    async def _safe_precompute_delight_scores(
        self,
        *,
        profile: SoulProfile,
        limit: int,
    ) -> int:
        """Detached-task wrapper for precompute_delight_scores (v0.3.62+).

        ``precompute_pool_copy`` schedules this as ``asyncio.create_task``
        instead of awaiting it inline. The previous shared
        ``_precompute_lock`` made delight scoring stall the next
        expression batch whenever the LLM was slow on delight calls —
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
                        item.bvid,
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
        from openbiliclaw.discovery.engine import VALID_STYLE_KEYS
        from openbiliclaw.llm.prompts import build_batch_content_evaluation_prompt

        profile_data = _recommendation_profile_summary(profile)
        content_items = [
            {
                "bvid": c.bvid,
                "content_id": c.content_id or c.bvid,
                "title": c.title,
                "up_name": c.up_name or c.author_name,
                "description": (c.description or "")[:200],
                "duration": c.duration,
                "view_count": c.view_count,
                "source_strategy": c.source_strategy,
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
            content_items=content_items,
            source_context=batch[0].source_strategy if batch else "",
            source_platform=platform,
            negative_examples=negative_examples,
        )

        response = await self._llm.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            max_tokens=8192,
            # v0.3.51+: structured XHS classification — pure score +
            # categorical fields, doesn't benefit from reasoning chain.
            reasoning_effort="",
            caller="recommendation.evaluate_batch",
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
            reason = str(result.get("reason", "")).strip()
            topic_group = str(result.get("topic_group", "")).strip()
            style_key = str(result.get("style_key", "")).strip().lower()

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
        """Score un-scored pool candidates for proactive delight potential.

        Two-stage retrieval:
          1. Coarse: ``get_pool_candidates_needing_delight_score`` filters
             by ``relevance_score >= 0.55`` and orders by relevance DESC,
             capped at ``limit`` (default 50). Free — uses scores already
             computed by discovery's ``evaluate_batch``.
          2. Fine: ``LLMDelightScorer.score_batch`` LLM-judges those 50
             against a delight rubric (cross-domain bridge / hidden need /
             quality, not naive similarity).

        Default ``limit=50`` (raised from 30 once relevance gate landed):
        more head-room for the LLM to find true delights without burning
        cycles on weak-fit junk. Cost: 50/5 = 10 batches × ~¥0.01 ≈
        ¥0.10/cycle, ¥0.80/day at 8 cycles.
        """
        from openbiliclaw.recommendation.delight import LLMDelightScorer

        scorer = LLMDelightScorer(llm_service=self._llm)

        prefs = getattr(profile, "preferences", None)
        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))
        effective_threshold = scorer.effective_threshold(exploration_openness)
        rows = self._database.get_pool_candidates_needing_delight_score(
            limit=limit,
            min_delight_score_for_reason=effective_threshold,
            xhs_self_nickname=self._xhs_self_nickname(),
        )
        if not rows:
            return 0

        candidates = self._rows_to_discovered(rows)

        # All ``rows`` returned here either lack a delight_score, or have
        # a stale one from the embedding-era scorer (which we choose to
        # re-judge with the LLM rather than trust). Send them all through
        # one batched LLM scoring pass — no special-case backfill loop.
        scored_count = 0
        to_score: list[Any] = list(candidates)

        try:
            scored = await scorer.score_batch(to_score, profile)
        except Exception:
            logger.exception("Delight LLM batch scoring failed for %d candidates", len(to_score))
            return 0

        for candidate in to_score:
            result = scored.get(candidate.bvid)
            if result is None:
                # LLM dropped this one — mark with sentinel score so it's
                # not picked again next cycle, but record nothing positive.
                self._database.update_delight_score(
                    candidate.bvid,
                    delight_score=0.01,
                    delight_reason="",
                    delight_hook="",
                )
                continue

            persisted_score = max(0.01, result.score)
            if result.score < effective_threshold:
                # Below threshold — persist score but no reason/hook
                self._database.update_delight_score(
                    candidate.bvid,
                    delight_score=persisted_score,
                    delight_reason="",
                    delight_hook="",
                )
                scored_count += 1
                continue

            # Above threshold — LLM already provided rationale + hook
            # in the same call, no extra LLM trip needed.
            self._database.update_delight_score(
                candidate.bvid,
                delight_score=persisted_score,
                delight_reason=result.rationale or "",
                delight_hook=result.hook or "意外契合",
            )
            scored_count += 1
            logger.info(
                "Delight candidate found: %s (score=%.3f, hook=%s)",
                candidate.bvid,
                persisted_score,
                result.hook,
            )

        return scored_count

    async def _generate_delight_reason(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
        reason_stub: str,
    ) -> tuple[str, str]:
        """Generate a delight reason explanation via LLM.

        Returns:
            (delight_reason, delight_hook) tuple.
        """
        from openbiliclaw.llm.prompts import build_delight_reason_prompt

        tone_profile = self._expression_tone_profile(profile, content)
        messages = build_delight_reason_prompt(
            profile_summary=_recommendation_profile_summary(
                profile,
                include_active_insights=True,
            ),
            content_summary={
                "title": content.title,
                "up_name": content.up_name,
                "description": (content.description or "")[:300],
                "source_strategy": content.source_strategy,
                "style_key": content.style_key,
                "topic_group": content.topic_group,
                "relevance_score": content.relevance_score,
            },
            reason_stub=reason_stub,
            tone_profile=tone_profile,
            source_platform=content.source_platform or "bilibili",
        )
        try:
            response = await self._llm.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                caller="recommendation.delight_reason",
            )
            payload = extract_llm_json_object(
                str(response.content),
                wrapper_keys=("result", "item", "data", "output"),
                item_predicate=lambda item: "delight_reason" in item or "delight_hook" in item,
            )
            if payload is None:
                raise ValueError("Delight reason response must be a JSON object.")
            reason = str(payload.get("delight_reason", "")).strip()
            hook = str(payload.get("delight_hook", "")).strip()
            if reason and hook:
                return (reason, hook)
        except Exception:
            logger.exception(
                "Failed to generate delight reason for %s",
                content.bvid,
            )
        # Fallback
        return ("这条可能会给你意外的惊喜", "意外惊喜")

    async def _precompute_batch(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
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
                "description": (item.description or "")[:200],
                "source_strategy": item.source_strategy,
                "style_key": item.style_key,
                "topic_group": item.topic_group,
                "relevance_score": item.relevance_score,
            }
            for item in batch
        ]
        messages = build_batch_expression_prompt(
            profile_summary=_recommendation_profile_summary(profile),
            content_items=content_items,
            tone_profile=tone_profile,
            source_platform=batch[0].source_platform if batch else "bilibili",
        )

        try:
            response = await self._llm.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=8192,
                # v0.3.51+: expression generation is short copy
                # writing per item — reasoning chain just bloats
                # output (write_expression cost ~3x with reasoning
                # vs without, no quality difference).
                reasoning_effort="",
                caller="recommendation.write_expression",
            )
            payload = extract_llm_json_list(
                str(response.content),
                wrapper_keys=("results", "items", "expressions", "data"),
                allow_singleton=True,
                item_predicate=lambda item: "expression" in item or "topic_label" in item,
            )
            if payload is None:
                raise ValueError("Expected expression JSON array or compatible wrapper.")
        except Exception as exc:
            if is_llm_rate_limit_error(exc):
                logger.warning(
                    "Batch expression generation skipped single-item fallback for %d items "
                    "because the LLM provider is rate-limited or cooling down: %s",
                    len(batch),
                    exc,
                )
                return 0
            logger.warning(
                "Batch expression generation failed for %d items, falling back to single",
                len(batch),
            )
            return await self._precompute_single_fallback(batch, profile)

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
            logger.warning(
                "Batch expression response carried no bvid/content_id for %d "
                "items; positional matching is unreliable, falling back to "
                "single generation",
                len(batch),
            )
            return await self._precompute_single_fallback(batch, profile)

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
            expression = str(result.get("expression", "")).strip()
            topic_label = str(result.get("topic_label", "")).strip()
            if not expression or not topic_label:
                continue
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
        return completed

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
        limit: int = 5,
    ) -> list[Recommendation]:
        """Instantly pick a new batch from the discovery pool.

        Delegates to :meth:`serve` with ``expression_mode="precomputed"``.
        """
        return await self.serve(profile, limit=limit, expression_mode="precomputed")

    async def append_recommendations(
        self,
        *,
        profile: SoulProfile,
        excluded_bvids: list[str],
        limit: int = 10,
    ) -> list[Recommendation]:
        """Append another page of recommendations from the discovery pool.

        Delegates to :meth:`serve` with excluded BVIDs for pagination.
        """
        excluded = frozenset(b.strip() for b in excluded_bvids if b and b.strip())
        return await self.serve(
            profile,
            limit=limit,
            excluded_bvids=excluded,
            expression_mode="precomputed",
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

        messages = build_recommendation_expression_prompt(
            profile_summary=_recommendation_profile_summary(
                profile,
                interests=interests_for_prompt,
            ),
            content_summary={
                "title": content.title,
                "up_name": content.up_name,
                "description": content.description,
                "source_strategy": content.source_strategy,
                "style_key": content.style_key,
                "topic_group": content.topic_group,
                "relevance_score": content.relevance_score,
            },
            tone_profile=tone_profile,
            source_platform=content.source_platform or "bilibili",
        )
        try:
            response = await self._llm.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                caller="recommendation.expression",
            )
            payload = extract_llm_json_object(
                str(response.content),
                wrapper_keys=("result", "item", "expression", "data", "output"),
                item_predicate=lambda item: "expression" in item or "topic_label" in item,
            )
            if payload is None:
                raise ValueError("Expression response must be a JSON object.")
            expression = str(payload.get("expression", "")).strip()
            topic_label = str(payload.get("topic_label", "")).strip()
            if expression and topic_label:
                return (expression, topic_label)
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
        style_key = RecommendationEngine._style_token(content)
        if style_key in {"lifestyle", "fun_variety", "light_chat"}:
            adjusted = _clone_tone_profile(tone)
            adjusted["density"] = "light"
            if adjusted["playfulness"] == "low":
                adjusted["playfulness"] = "medium"
            return adjusted
        if style_key in {"story_doc", "review_roundup", "visual_showcase"}:
            adjusted = _clone_tone_profile(tone)
            if adjusted["density"] == "dense":
                adjusted["density"] = "balanced"
            return adjusted
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
    def _ranking_key(item: DiscoveredContent) -> tuple[int, float, float, int, str]:
        return (
            0 if item.candidate_tier == "primary" else 1,
            -item.relevance_score,
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
        style_key = content.style_key.strip()
        if style_key == "game_strategy":
            return f"《{title}》偏你会点开的那种机制/攻略向，不只是热闹，重点是真有东西能翻。"
        if style_key == "news_brief":
            return f"《{title}》这条胜在信息来得快，而且不是纯复读，适合你先抓重点。"
        if style_key == "practical_guide":
            return f"《{title}》偏实操一点，信息是能直接拿来用的，不会只有概念。"
        if style_key == "story_doc":
            return f"《{title}》这条没那么硬，但会把故事和信息一起带出来，适合你换口气的时候看。"
        if style_key == "visual_showcase":
            return f"《{title}》更偏轻一点，适合你换换脑子，但内容不空。"
        if style_key == "tech_analysis":
            return f"《{title}》这条偏技术拆解，但入口不算高，适合你先抓重点再决定要不要细看。"
        if style_key == "deep_dive":
            return f"《{title}》还是你常吃那一路，偏讲透来龙去脉，不会只给结论。"
        if style_key == "fun_variety":
            return f"《{title}》这条更偏轻松整活，拿来换个脑子刚好，也不是纯吵闹。"
        if style_key == "lifestyle":
            return f"《{title}》这条是轻一点的生活向，顺手点开不累，氛围和信息都还在线。"
        if style_key == "review_roundup":
            return f"《{title}》这种盘点/测评向比较省力，先快速过一遍重点会很顺。"
        if style_key == "light_chat":
            return f"《{title}》这条不是硬讲解那路，胜在讲得顺、看着不累，适合随手点开。"
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
    ) -> list[DiscoveredContent]:
        if score_override:
            ranked = sorted(
                candidates,
                key=lambda item: -score_override.get(item.bvid, 0.0),
            )
        else:
            ranked = sorted(candidates, key=cls._ranking_key)
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

        def _relevance(item: DiscoveredContent) -> float:
            if score_override:
                return float(score_override.get(item.bvid, 0.0))
            return float(item.relevance_score or 0.0)

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
            logger.info(
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
        if style_key == "lifestyle":
            return 6
        if style_key == "fun_variety":
            return 5
        if style_key == "light_chat":
            return 4
        if style_key == "review_roundup":
            return 3
        if style_key == "story_doc":
            return 2
        if style_key == "visual_showcase":
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
        token = RecommendationEngine._normalize_topic_token(item.style_key)
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
                cover_url=str(row.get("cover_url", "")),
                view_count=int(row.get("view_count", 0) or 0),
                like_count=int(row.get("like_count", 0) or 0),
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
            )
            for row in rows
        ]

    def _load_pool_candidates(self, *, limit: int) -> list[DiscoveredContent]:
        rows = self._database.get_pool_candidates(
            limit=limit, xhs_self_nickname=self._xhs_self_nickname()
        )
        return self._rows_to_discovered(rows)

    def _load_pool_candidates_needing_copy(self, *, limit: int) -> list[DiscoveredContent]:
        rows = self._database.get_pool_candidates_needing_copy(
            limit=limit, xhs_self_nickname=self._xhs_self_nickname()
        )
        return self._rows_to_discovered(rows)

    def _exclude_recently_viewed(
        self,
        candidates: list[DiscoveredContent],
    ) -> list[DiscoveredContent]:
        viewed_bvids = self._database.get_recent_viewed_bvids()
        if not viewed_bvids:
            return candidates
        return [item for item in candidates if item.bvid not in viewed_bvids]

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
