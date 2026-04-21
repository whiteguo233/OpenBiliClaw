"""Recommendation Engine — ranking, expression, and delivery.

Handles the final stage: taking discovered content and presenting it
to the user in a warm, friend-like manner with deep personal insights.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from openbiliclaw.soul.tone import build_tone_profile

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.recommendation.curator import PoolCurator
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
    ) -> LLMResponse: ...


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
        embedding_service: object | None = None,
    ) -> None:
        self._llm = llm
        self._database = database
        self._curator = curator
        self._embedding_service = embedding_service
        self._classify_lock = asyncio.Lock()

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
        multiplier = 4 if excluded_bvids else 3
        candidates = self._load_pool_candidates(limit=max(limit * multiplier, 40))
        if excluded_bvids:
            candidates = [c for c in candidates if c.bvid not in excluded_bvids]
        candidates = self._exclude_recently_viewed(candidates)
        label = "realtime" if expression_mode == "realtime" else "pool"
        logger.info(
            "Recommendation candidate summary (serve/%s): %s",
            label,
            json.dumps(self._build_debug_summary(candidates), ensure_ascii=False),
        )

        # No embedding API calls on the serve() hot path.
        # topic_group normalization and semantic scoring happen during
        # background discovery/precompute. serve() is pure DB + CPU.
        score_override: dict[str, float] | None = None
        if self._curator is not None:
            context = self._curator.build_context()
            score_override = self._curator.score_candidates(candidates, context)

        ranked = self._select_diversified_batch(
            candidates, limit=limit, score_override=score_override,
        )
        logger.info(
            "Recommendation picked summary (serve/%s): %s",
            label,
            json.dumps(self._build_debug_summary(ranked), ensure_ascii=False),
        )

        recommendations: list[Recommendation] = []
        shown_bvids: list[str] = []
        for item in ranked:
            rec = Recommendation(
                content=item,
                confidence=item.relevance_score,
                presented=False,
            )
            if expression_mode == "precomputed":
                rec.expression = item.pool_expression.strip()
                rec.topic_label = item.pool_topic_label.strip()
                # Fallback when precomputed copy is missing
                if not rec.expression:
                    rec.expression = self._fallback_expression(item)
                if not rec.topic_label:
                    rec.topic_label = self._fallback_topic_label(profile)
            rec.recommendation_id = self._database.insert_recommendation(
                item.bvid,
                confidence=rec.confidence,
                expression=rec.expression,
                topic=rec.topic_label,
                presented=0,
            )
            if expression_mode == "realtime":
                rec.expression, rec.topic_label = await self.generate_expression(
                    item, profile,
                )
                self._database.update_recommendation_content(
                    rec.recommendation_id,
                    expression=rec.expression,
                    topic=rec.topic_label,
                )
            recommendations.append(rec)
            shown_bvids.append(item.bvid)

        self._database.mark_pool_items_shown(shown_bvids)
        return recommendations

    async def _normalize_topic_groups(
        self,
        candidates: list[DiscoveredContent],
    ) -> None:
        """Use embedding similarity to unify semantically identical topic_keys
        that lack a topic_group, assigning them to an existing group.

        topic_group is already a coarse human-readable category set by Discovery.
        Re-merging these via embedding produces false positives (e.g. "人工智能"
        and "国际史实" landing in the same bucket at threshold 0.82) because short
        Chinese labels are deceptively close in embedding space.

        This method therefore only operates on items WITHOUT a topic_group,
        attempting to assign them to a group from items that already have one.
        """
        if self._embedding_service is None or not candidates:
            return

        from openbiliclaw.llm.embedding import cosine_similarity

        # Build cluster centroids from items that already have a topic_group
        clusters: dict[str, list[float]] = {}
        for item in candidates:
            group = (item.topic_group or "").strip().lower()
            if not group or group in clusters:
                continue
            vec = await self._embedding_service.embed(group)
            if vec:
                clusters[group] = vec

        if not clusters:
            return

        # Only try to assign topic_group to items that don't have one
        # Use stricter threshold for short Chinese labels (default 0.82 is too low)
        threshold = min(0.92, self._embedding_service.similarity_threshold + 0.10)
        for item in candidates:
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
                    "Rec topic assigned: %r → group %r (sim=%.3f)",
                    topic, best_label, best_sim,
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
            interest_vec = await self._embedding_service.embed(str(interest["name"]))
            if not interest_vec:
                scored.append((interest, float(interest.get("weight", 0))))
                continue
            sim = cosine_similarity(content_vec, interest_vec)
            # Blend embedding similarity with weight for ranking
            blended = sim * 0.7 + float(interest.get("weight", 0)) * 0.3
            scored.append((interest, blended))

        scored.sort(key=lambda x: -x[1])
        return [item for item, _ in scored[:top_k]]

    async def precompute_pool_copy(
        self,
        *,
        profile: SoulProfile,
        limit: int = 20,
        delight_limit: int = 30,
        batch_size: int = 8,
    ) -> int:
        """Precompute fast-path popup copy for fresh pool candidates.

        Uses batched LLM calls: one call generates expressions for up to
        ``batch_size`` items, reducing API calls by ~8x.

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
        # Safety net: classify any leftover un-evaluated items that were
        # not caught by the ingest-time classification (e.g. race condition,
        # or the background task was suppressed).  This is a no-op when all
        # pool items already have style_key and topic_group.
        try:
            await self.classify_pool_backlog(profile=profile, limit=limit)
        except Exception:
            logger.exception("classify_pool_backlog failed, continuing with precompute")

        candidates = self._load_pool_candidates_needing_copy(limit=max(0, limit))
        if not candidates:
            # Even when no expression work is needed, still run delight scoring
            await self.precompute_delight_scores(
                profile=profile, limit=delight_limit,
            )
            return 0

        completed = 0
        for batch_start in range(0, len(candidates), batch_size):
            batch = candidates[batch_start:batch_start + batch_size]
            count = await self._precompute_batch(batch, profile)
            completed += count

        # Run delight scoring after expression precompute
        await self.precompute_delight_scores(
            profile=profile, limit=delight_limit,
        )
        return completed

    # ── Source-agnostic content classification ───────────────────────
    #
    # Content from any source (bilibili, xiaohongshu, web, …) must carry
    # the same set of content features (style_key, topic_group,
    # relevance_score) before it enters the diversity/ranking pipeline.
    # Items that lack these features would collapse _select_diversified_batch
    # — all sharing "unknown" style and a single fallback topic token.
    #
    # classify_pool_backlog() is the single gate: it picks up un-classified
    # items in the pool, runs them through the same LLM evaluation used for
    # bilibili discovery, and writes results back.  After this step content
    # is truly source-agnostic — the recommendation layer only sees content
    # features, never platform labels.

    async def classify_pool_backlog(
        self,
        *,
        profile: SoulProfile,
        limit: int = 30,
        batch_size: int = 10,
    ) -> int:
        """Classify pool items that lack content features (style / topic / score).

        Content enters the pool from many sources.  Bilibili content is
        classified during discovery, but other sources (xiaohongshu, web, …)
        may bypass that pipeline and arrive with empty ``style_key``,
        ``topic_group``, and ``relevance_score``.

        This method is the recommendation module's guarantee of
        source-agnostic treatment: every item that reaches the ranking
        pipeline has proper content features, regardless of where it came
        from.  It reuses the same LLM evaluation prompt that discovery uses,
        so the feature space is identical across all sources.

        Returns:
            Number of items classified.
        """
        if self._classify_lock.locked():
            return 0  # Another classify task is already running
        async with self._classify_lock:
            return await self._classify_pool_backlog_locked(
                profile=profile, limit=limit, batch_size=batch_size,
            )

    async def _classify_pool_backlog_locked(
        self,
        *,
        profile: SoulProfile,
        limit: int,
        batch_size: int,
    ) -> int:
        """Inner implementation of classify_pool_backlog, called under lock."""
        rows = self._database.get_pool_candidates_needing_evaluation(limit=limit)
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
                    "classify_pool_backlog: batch failed (%d items)", len(batch),
                )
                continue

            # Persist results back to the pool.
            for item in batch:
                # Use topic_group as topic_key when the original is empty —
                # diversity tokens fall back to topic_key, so this is critical.
                if not item.topic_key and item.topic_group:
                    item.topic_key = item.topic_group
                try:
                    self._database.cache_content(
                        item.bvid, **item.to_cache_kwargs(),
                    )
                    classified += 1
                except Exception:
                    logger.exception(
                        "classify_pool_backlog: failed to persist %s", item.bvid,
                    )

        logger.info(
            "classify_pool_backlog: %d/%d items classified "
            "(styles: %s, topics: %s)",
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
                "title": c.title,
                "up_name": c.up_name or c.author_name,
                "description": (c.description or "")[:200],
                "duration": c.duration,
                "view_count": c.view_count,
                "source_strategy": c.source_strategy,
            }
            for c in batch
        ]
        # Determine the dominant platform for prompt context
        platform = (batch[0].source_platform or "bilibili") if batch else "bilibili"
        messages = build_batch_content_evaluation_prompt(
            profile_summary=profile_data,
            content_items=content_items,
            source_context=batch[0].source_strategy if batch else "",
            source_platform=platform,
        )

        response = await self._llm.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            max_tokens=8192,
        )
        raw = str(getattr(response, "content", "")).strip()
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            raise ValueError(f"Expected JSON array, got {type(payload).__name__}")

        if len(payload) != len(batch):
            logger.warning(
                "LLM returned %d results for %d items in classification batch",
                len(payload), len(batch),
            )

        for i, content in enumerate(batch):
            if i >= len(payload) or not isinstance(payload[i], dict):
                # Mark as attempted so get_pool_candidates_needing_evaluation
                # won't retry this item forever.  A score of 0.01 signals
                # "classification attempted but no usable result".
                content.relevance_score = 0.01
                content.relevance_reason = "classification_failed"
                continue
            result = payload[i]
            score = max(0.0, min(1.0, float(result.get("score", 0.0))))
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
        limit: int = 30,
    ) -> int:
        """Score un-scored pool candidates for proactive delight potential.

        Items scoring above the delight threshold get LLM-generated
        delight_reason explanations persisted to the database.
        """
        from openbiliclaw.recommendation.delight import DelightScorer

        scorer = DelightScorer(
            embedding_service=self._embedding_service,
            database=self._database,
        )

        rows = self._database.get_pool_candidates_needing_delight_score(limit=limit)
        if not rows:
            return 0

        candidates = self._rows_to_discovered(rows)

        prefs = getattr(profile, "preferences", None)
        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))
        effective_threshold = scorer.effective_threshold(exploration_openness)

        scored_count = 0
        for candidate in candidates:
            try:
                delight_score, signals, reason_stub = await scorer.score(
                    candidate, profile,
                )
            except Exception:
                logger.exception(
                    "Delight scoring failed for %s, writing score=0.01 to skip next cycle",
                    candidate.bvid,
                )
                delight_score = 0.01  # Mark as scored (non-zero) to skip next time
                self._database.update_delight_score(
                    candidate.bvid,
                    delight_score=delight_score,
                    delight_reason="",
                    delight_hook="",
                )
                continue

            if delight_score < effective_threshold:
                # Below threshold — persist score but no reason
                self._database.update_delight_score(
                    candidate.bvid,
                    delight_score=max(0.01, delight_score),
                    delight_reason="",
                    delight_hook="",
                )
                scored_count += 1
                continue

            # Above threshold — generate delight reason via LLM
            delight_reason, delight_hook = await self._generate_delight_reason(
                candidate, profile, reason_stub,
            )
            self._database.update_delight_score(
                candidate.bvid,
                delight_score=delight_score,
                delight_reason=delight_reason,
                delight_hook=delight_hook,
            )
            scored_count += 1
            logger.info(
                "Delight candidate found: %s (score=%.3f, hook=%s)",
                candidate.bvid, delight_score, delight_hook,
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
            )
            payload = json.loads(response.content.strip())
            if not isinstance(payload, dict):
                raise ValueError("Delight reason response must be a JSON object.")
            reason = str(payload.get("delight_reason", "")).strip()
            hook = str(payload.get("delight_hook", "")).strip()
            if reason and hook:
                return (reason, hook)
        except Exception:
            logger.exception(
                "Failed to generate delight reason for %s", content.bvid,
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
            )
            payload = json.loads(response.content.strip())
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                raise ValueError(f"Expected JSON array, got {type(payload).__name__}")
        except Exception:
            logger.warning(
                "Batch expression generation failed for %d items, falling back to single",
                len(batch),
            )
            return await self._precompute_single_fallback(batch, profile)

        completed = 0
        for i, item in enumerate(batch):
            if i >= len(payload) or not isinstance(payload[i], dict):
                continue
            expression = str(payload[i].get("expression", "")).strip()
            topic_label = str(payload[i].get("topic_label", "")).strip()
            if not expression or not topic_label:
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
            )
            payload = json.loads(response.content.strip())
            if not isinstance(payload, dict):
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
    ) -> dict[str, str]:
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
            tone = dict(tone)
            tone["density"] = "light"
            if tone["playfulness"] == "low":
                tone["playfulness"] = "medium"
            return tone
        if style_key in {"story_doc", "review_roundup", "visual_showcase"}:
            tone = dict(tone)
            if tone["density"] == "dense":
                tone["density"] = "balanced"
            return tone
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
        return (
            f"《{title}》这条切口挺顺的，先丢给你看看，"
            "说不定正好能对上你当下的兴趣。"
        )

    @staticmethod
    def _fallback_topic_label(profile: SoulProfile) -> str:
        if profile.core_traits:
            return f"你最近那股偏{profile.core_traits[0]}的状态"
        return "想先丢给你的一条"

    @classmethod
    def _select_diversified_batch(
        cls,
        candidates: list[DiscoveredContent],
        *,
        limit: int,
        score_override: dict[str, float] | None = None,
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
        selected: list[DiscoveredContent] = []
        deferred: list[DiscoveredContent] = []
        topic_counts: dict[str, int] = {}
        broad_topic_counts: dict[str, int] = {}
        style_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        seen_sources: set[str] = set()
        per_source_cap = cls._source_cap(limit)
        available_sources = {
            cls._normalize_topic_token(item.source_strategy)
            for item in ranked
            if cls._normalize_topic_token(item.source_strategy)
        }
        unique_source_target = min(limit, len(available_sources))

        # Source diversity floor: pre-reserve best item per source
        # so that no source gets completely crowded out
        reserved = cls._reserve_per_source(ranked, available_sources)
        reserved_bvids = {item.bvid for item in reserved}

        def _exceeds_broad_cap(item: DiscoveredContent) -> bool:
            bt = cls._broad_topic_token(item)
            return bool(bt) and broad_topic_counts.get(bt, 0) >= broad_cap

        def _track_broad(item: DiscoveredContent) -> None:
            bt = cls._broad_topic_token(item)
            if bt:
                broad_topic_counts[bt] = broad_topic_counts.get(bt, 0) + 1

        for item in ranked:
            tokens = cls._diversity_tokens(item)
            style_token = cls._style_token(item)
            source_token = cls._normalize_topic_token(item.source_strategy)
            # Reserved items bypass broad/style/source caps (not topic_cap)
            # when their source is not yet represented — guarantees source floor
            needs_source_floor = (
                item.bvid in reserved_bvids
                and bool(source_token)
                and source_token not in seen_sources
            )
            prioritize_new_source = (
                bool(source_token)
                and source_token not in seen_sources
                and len(seen_sources) < unique_source_target
            )
            # Topic dedup is always enforced — even reserved items
            if tokens and any(
                topic_counts.get(token, 0) >= per_topic_cap for token in tokens
            ):
                deferred.append(item)
                continue
            if not needs_source_floor and _exceeds_broad_cap(item):
                deferred.append(item)
                continue
            if (
                not needs_source_floor
                and not prioritize_new_source
                and style_counts.get(style_token, 0) >= per_style_cap
            ):
                deferred.append(item)
                continue
            if (
                not needs_source_floor
                and source_token
                and source_counts.get(source_token, 0) >= per_source_cap
            ):
                deferred.append(item)
                continue
            if (
                not needs_source_floor
                and not prioritize_new_source
                and source_token
                and source_token in seen_sources
            ):
                deferred.append(item)
                continue
            selected.append(item)
            for token in tokens:
                topic_counts[token] = topic_counts.get(token, 0) + 1
            _track_broad(item)
            style_counts[style_token] = style_counts.get(style_token, 0) + 1
            if source_token:
                seen_sources.add(source_token)
                source_counts[source_token] = source_counts.get(source_token, 0) + 1
            if len(selected) >= limit:
                return _finalize(selected)

        def try_fill(
            pool: list[DiscoveredContent],
            *,
            topic_cap: int,
            enforce_style_cap: bool,
            enforce_source_cap: bool,
            enforce_broad_cap: bool,
        ) -> list[DiscoveredContent]:
            remaining: list[DiscoveredContent] = []
            for item in pool:
                tokens = cls._diversity_tokens(item)
                style_token = cls._style_token(item)
                source_token = cls._normalize_topic_token(item.source_strategy)
                if tokens and any(topic_counts.get(token, 0) >= topic_cap for token in tokens):
                    remaining.append(item)
                    continue
                if enforce_broad_cap and _exceeds_broad_cap(item):
                    remaining.append(item)
                    continue
                if (
                    enforce_style_cap
                    and style_counts.get(style_token, 0) >= per_style_cap
                ):
                    remaining.append(item)
                    continue
                if (
                    enforce_source_cap
                    and source_token
                    and source_counts.get(source_token, 0) >= per_source_cap
                ):
                    remaining.append(item)
                    continue
                selected.append(item)
                for token in tokens:
                    topic_counts[token] = topic_counts.get(token, 0) + 1
                _track_broad(item)
                style_counts[style_token] = style_counts.get(style_token, 0) + 1
                if source_token:
                    source_counts[source_token] = source_counts.get(source_token, 0) + 1
                if len(selected) >= limit:
                    return []
            return remaining

        remaining = try_fill(
            deferred,
            topic_cap=per_topic_cap,
            enforce_style_cap=True,
            enforce_source_cap=True,
            enforce_broad_cap=True,
        )
        if len(selected) < limit:
            remaining = try_fill(
                remaining,
                topic_cap=per_topic_cap,
                enforce_style_cap=False,
                enforce_source_cap=True,
                enforce_broad_cap=True,
            )
        if len(selected) < limit:
            remaining = try_fill(
                remaining,
                topic_cap=per_topic_cap,
                enforce_style_cap=False,
                enforce_source_cap=False,
                enforce_broad_cap=True,
            )
        if len(selected) < limit:
            remaining = try_fill(
                remaining,
                topic_cap=soft_topic_cap,
                enforce_style_cap=False,
                enforce_source_cap=False,
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
                if bt and broad_topic_counts.get(bt, 0) >= fallback_broad_cap:
                    continue
                selected.append(item)
                if bt:
                    broad_topic_counts[bt] = broad_topic_counts.get(bt, 0) + 1
                style_counts[style_token] = style_counts.get(style_token, 0) + 1
                if len(selected) >= limit:
                    break
        return _finalize(selected)

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

        weakest_score = min(
            cls._effective_score(item, score_override) for item in selected
        )
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
                    remaining_topics.get(token, 0) >= topic_cap
                    for token in candidate_tokens
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
        fallback_fields.extend(
            m for m in re.findall(r"[\u4e00-\u9fff]{2,4}", title)
        )
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
        cls, items: list[DiscoveredContent],
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
    def _reserve_per_source(
        ranked: list[DiscoveredContent],
        available_sources: set[str],
    ) -> list[DiscoveredContent]:
        """Pick the best candidate per source to guarantee source diversity floor."""
        reserved: list[DiscoveredContent] = []
        seen_sources: set[str] = set()
        for item in ranked:
            source = RecommendationEngine._normalize_topic_token(item.source_strategy)
            if source and source in available_sources and source not in seen_sources:
                seen_sources.add(source)
                reserved.append(item)
                if len(seen_sources) >= len(available_sources):
                    break
        return reserved

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
    def _source_cap(limit: int) -> int:
        return 2 if limit <= 5 else 3

    @staticmethod
    def _platform_token(item: DiscoveredContent) -> str:
        """Platform label for observability only — not used to filter picks.

        Diversity and caps are driven by content features (style, topic,
        source_strategy). This is exposed in ``_build_debug_summary`` so
        log readers can still see the platform split per round.
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
        rows = self._database.get_pool_candidates(limit=limit)
        return self._rows_to_discovered(rows)

    def _load_pool_candidates_needing_copy(self, *, limit: int) -> list[DiscoveredContent]:
        rows = self._database.get_pool_candidates_needing_copy(limit=limit)
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
    ) -> dict[str, object]:
        style_counts = Counter(
            cls._style_token(item) or "unknown" for item in candidates
        )
        source_counts = Counter(
            cls._normalize_topic_token(item.source_strategy) or "unknown"
            for item in candidates
        )
        platform_counts = Counter(cls._platform_token(item) for item in candidates)
        topic_counts: Counter[str] = Counter()
        for item in candidates:
            tokens = cls._diversity_tokens(item)
            if not tokens:
                topic_counts["unknown"] += 1
                continue
            topic_counts[sorted(tokens)[0]] += 1
        return {
            "count": len(candidates),
            "platforms": dict(platform_counts.most_common()),
            "styles": dict(style_counts.most_common(5)),
            "sources": dict(source_counts.most_common(5)),
            "topics": dict(topic_counts.most_common(5)),
            "sample_titles": [item.title for item in candidates[:5]],
        }
