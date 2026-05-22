"""Delight Scorer — identifies content that would surprise and delight the user.

Computes a composite ``delight_score`` that measures how deeply a piece of
content resonates with the user's soul profile — not just surface interests,
but deep needs, active insight hypotheses, and latent curiosity patterns.

This score is deliberately separate from the PoolCurator's ``rec_score``
(which handles freshness/fatigue/monotony for the regular recommendation
batch). The delight score focuses on **deep resonance**, not recency or
diversity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.llm.json_utils import extract_llm_json_list
from openbiliclaw.llm.prompts import build_delight_score_batch_prompt

if TYPE_CHECKING:
    from openbiliclaw.llm.embedding import SupportsEmbeddingService

logger = logging.getLogger(__name__)


class SupportsDelightCandidate(Protocol):
    bvid: str
    title: str
    description: str
    view_count: int
    like_count: int
    topic_key: str
    topic_group: str
    source_strategy: str
    relevance_score: float


class SupportsRecommendationSignalStore(Protocol):
    def get_recent_recommendation_signals(self, *, limit: int = ...) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DelightSignals:
    """Individual signal components that compose the delight score."""

    deep_need_alignment: float = 0.0
    insight_resonance: float = 0.0
    # Embedding match against the user's actual top likes — this is the
    # signal that carries entertainment / playful axes that deep_needs
    # alone misses (deep_needs leans analytical for many users).
    likes_alignment: float = 0.0
    novelty_factor: float = 0.0
    quality_indicator: float = 0.0
    exploration_match: float = 0.0
    # Embedding match against the user's disliked_topics. Higher means
    # the content semantically resembles something they explicitly
    # rejected → subtracts from final score.
    dislike_penalty: float = 0.0


@dataclass(frozen=True)
class DelightWeights:
    """Tuneable weights for the composite delight score."""

    deep_need: float = 0.20
    insight: float = 0.15
    likes: float = 0.30
    novelty: float = 0.15
    quality: float = 0.10
    exploration: float = 0.10
    # Multiplier applied to dislike_penalty before it's subtracted from
    # the positive sum.  0.50 means a strongly disliked match (penalty=1.0)
    # subtracts 0.50 from the score, typically pushing it below threshold.
    dislike_penalty: float = 0.50


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Delight threshold:
# Calibrated for the bge-m3 multilingual embedding (the user's main
# local embedding via Ollama). bge-m3's cosine distribution is
# tighter than Gemini's — even strong likes_alignment matches top out
# around raw cosine 0.85, which after the ``(sim - 0.5) * 2.0``
# amplifier yields ~0.70 contribution; combined with the 0.30 weight
# that's a 0.21 ceiling on the likes signal alone.
#
# History of this constant:
#   v0.3.31  0.65  Gemini-era; under bge-m3 unreachable, killed feature
#   v0.3.34  0.45  embedding-cosine empirical p95 (~4-5/100)
#   v0.3.35  0.44  re-tuned for ~12-18/600 (top-200 sample extrapolation)
#   v0.3.39  0.55  ↓ swap to LLM-judged scoring exposed a wider score
#                  distribution: with rubric-driven rationale, LLM
#                  generously gives 0.45-0.55 to "common-fit" content
#                  (hooks like 「常规关联」/「常规推荐」). Empirical
#                  observation 2026-05-04 on 193 scored items:
#                    0.44-0.60:  9   ← "common" tier — LLM hedging
#                    0.60-0.75: 14   ← real cross-domain delight
#                    0.75+   : 12   ← exceptional ("跨域惊喜")
#                  35/193 = 18.1% = ~116 in 642 pool — 7x the v0.3.35
#                  target. Lifting to 0.55 cuts the LLM's middle-of-rubric
#                  "common" tier (which doesn't read as surprise to the
#                  user) while keeping the 0.60+ band that's genuinely
#                  delightful. Expected: ~26/642 ≈ 4% pass rate.
#   v0.3.41  0.57  ↑ 0.55 still admitted ~26/642 which the user judged
#                  too many. 0.57 splits the "common" tail closer to 0.60
#                  where the LLM's reasoning starts to read as actual
#                  surprise rather than hedging. Expected: ~18-20/642
#                  ≈ 3% pass rate, tracking closer to v0.3.35's target.
# CONSERVATIVE bar shifts to 0.67 (proportionally tighter for low-
# exploration users — only the 0.67+ "scoring rationale = surprise" tier).
#
# v0.3.49: jump 0.57 → 0.70 to actually align with the LLM rubric in
# `_DELIGHT_BATCH_SCORE_SYSTEM_PROMPT`, which itself defines:
#   0.70-0.85: 跨域呼应,用户大概率会感兴趣但自己不会主动找  ← real delight
#   0.55-0.70: 有惊喜潜力但相对常规                          ← NOT delight
# 0.57 was 13 ticks below the LLM's own "actually delight" line, so
# every batch surfaced a flood of "相对常规" content as delight (35
# candidates in 43 minutes, hooks like "常规补给" / "实用工具" /
# "信息整合"). Bumping past the LLM's stated boundary cuts the false-
# positive rate ~60% — only items the scorer itself rated 跨域呼应
# tier or higher get pushed. CONSERVATIVE bar likewise shifts to 0.80
# (the next tier up — "用户不会主动找" + extra caution).
DEFAULT_DELIGHT_THRESHOLD: float = 0.70
CONSERVATIVE_DELIGHT_THRESHOLD: float = 0.80
_LOW_EXPLORATION_OPENNESS: float = 0.3
_DEFAULT_WEIGHTS = DelightWeights()


# Default batch size for LLM delight scoring. 5 keeps each prompt small
# (cache-friendly, fast) while still amortising the per-call HTTP/handshake
# cost. With ~30 candidates per refresh tick, that's 6 batched calls.
_DELIGHT_LLM_BATCH_SIZE: int = 5


class _SupportsStructuredLLM(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        max_tokens: int = ...,
        caller: str = ...,
    ) -> Any: ...


@dataclass
class DelightLLMResult:
    """One LLM-judged delight result for a candidate."""

    score: float = 0.0
    rationale: str = ""
    hook: str = ""


class LLMDelightScorer:
    """LLM-based delight scoring (replaces embedding-cosine pipeline).

    Why LLM instead of embedding cosine: embedding similarity rewards
    content that is *similar* to the user's existing likes, which is the
    opposite of "surprise". The pre-2026-05-04 ``DelightScorer`` therefore
    surfaced reinforcement content (more DeepSeek tutorials for an AI-heavy
    user) instead of cross-domain delights. An LLM evaluating a rubric
    can distinguish "concept-bridging" from "topic-repeating" in a way
    cosine cannot.

    Cost: at batch_size=5 and ~30 new candidates per refresh tick, that's
    6 LLM calls per cycle × ~¥0.01 = ¥0.06/cycle, ¥0.48/day at 8 cycles.
    Each call returns score + rationale + hook in one shot, eliminating
    the secondary ``_generate_delight_reason`` LLM hop.
    """

    def __init__(
        self,
        llm_service: _SupportsStructuredLLM,
        *,
        threshold: float = DEFAULT_DELIGHT_THRESHOLD,
        batch_size: int = _DELIGHT_LLM_BATCH_SIZE,
    ) -> None:
        self._llm_service = llm_service
        self._threshold = threshold
        self._batch_size = max(1, batch_size)

    @property
    def threshold(self) -> float:
        return self._threshold

    def effective_threshold(self, exploration_openness: float) -> float:
        """Return a possibly raised threshold for conservative users."""
        if exploration_openness < _LOW_EXPLORATION_OPENNESS:
            return max(self._threshold, CONSERVATIVE_DELIGHT_THRESHOLD)
        return self._threshold

    async def score_batch(
        self,
        candidates: list[SupportsDelightCandidate],
        profile: Any,
    ) -> dict[str, DelightLLMResult]:
        """Score a list of candidates via batched LLM calls.

        Returns a mapping ``bvid -> DelightLLMResult``. Items the LLM
        omits or mis-routes default to ``score=0.0``; callers should
        treat missing entries as "below threshold" and not retry the
        same batch (the LLM will keep dropping them — usually because
        the title was empty or untranslatable).
        """
        if not candidates:
            return {}

        results: dict[str, DelightLLMResult] = {}
        profile_summary = _build_delight_profile_summary(profile)

        for batch_start in range(0, len(candidates), self._batch_size):
            batch = candidates[batch_start : batch_start + self._batch_size]
            content_batch = [
                {
                    "bvid": c.bvid,
                    "title": (c.title or "")[:140],
                    "description": (c.description or "")[:280],
                    "topic_group": getattr(c, "topic_group", "") or "",
                    "source_strategy": getattr(c, "source_strategy", "") or "",
                    "relevance_score": round(float(c.relevance_score or 0.0), 3),
                }
                for c in batch
            ]
            messages = build_delight_score_batch_prompt(
                profile_summary=profile_summary,
                content_batch=content_batch,
            )
            try:
                response = await self._llm_service.complete_structured_task(
                    system_instruction=messages[0]["content"],
                    user_input=messages[1]["content"],
                    max_tokens=2048,
                    caller="recommendation.delight_score",
                )
            except Exception:
                logger.warning(
                    "Delight LLM batch scoring failed for %d candidates",
                    len(batch),
                    exc_info=True,
                )
                continue

            entries = _extract_delight_entries(
                str(getattr(response, "content", "")), expected_count=len(batch)
            )
            if not entries:
                logger.warning(
                    "Delight LLM batch produced 0 parseable entries for %d candidates "
                    "(provider response shape mismatch?)",
                    len(batch),
                )
                continue

            for entry in entries:
                bvid = str(entry.get("bvid", "")).strip()
                if not bvid:
                    continue
                results[bvid] = DelightLLMResult(
                    score=max(0.0, min(1.0, float(entry.get("score", 0.0) or 0.0))),
                    rationale=str(entry.get("rationale", "")).strip(),
                    hook=str(entry.get("hook", "")).strip(),
                )

        return results


def _extract_delight_entries(content: str, *, expected_count: int) -> list[dict[str, Any]]:
    """Extract a list of {bvid, score, rationale, hook} from an LLM response.

    Different LLM providers/models in JSON mode return different root
    shapes:
      - DeepSeek typically returns a clean ``[...]`` list (matching the
        prompt's <output_schema>).
      - mimo-v2.5-pro tends to wrap in an object: ``{"results": [...]}``,
        ``{"items": [...]}``, ``{"delights": [...]}``, or sometimes emits
        multiple root JSON objects newline-separated (causing
        ``json.JSONDecodeError: Extra data``).
      - Some models echo the schema as a single object when batch=1.

    This helper delegates to the shared LLM JSON extractor so array
    snippets, wrappers, singleton entries, and JSONL are handled the same
    way as recommendation/discovery parsers. Returns an empty list only if
    no valid entry could be extracted.
    """
    text = content.strip()
    if not text:
        return []

    entries = extract_llm_json_list(
        text,
        wrapper_keys=(
            "results",
            "items",
            "delights",
            "data",
            "scores",
            "candidates",
            "output",
            "list",
            "array",
        ),
        allow_singleton=True,
        item_predicate=lambda item: "bvid" in item or "score" in item,
    )
    if not entries:
        return []
    result = [dict(item) for item in entries]
    return result[:expected_count] if expected_count > 0 else result


def _build_delight_profile_summary(profile: Any) -> dict[str, object]:
    """Compact profile shape for the delight LLM prompt.

    Keeps only the fields the rubric actually uses:
      - top likes (domain + weight + first 4 specifics)
      - deep_needs
      - active_insights (hypothesis + confidence)
      - exploration_openness
      - disliked_topics (top 8)
    """
    summary: dict[str, object] = {}

    interest_layer = getattr(profile, "interest", None)
    likes_src = getattr(interest_layer, "likes", []) if interest_layer is not None else []
    likes_out: list[dict[str, object]] = []
    for d in sorted(
        likes_src,
        key=lambda dom: float(getattr(dom, "weight", 0.0) or 0.0),
        reverse=True,
    )[:8]:
        domain = str(getattr(d, "domain", "")).strip()
        if not domain:
            continue
        specs = [
            str(getattr(s, "name", "")).strip()
            for s in (getattr(d, "specifics", []) or [])[:4]
            if str(getattr(s, "name", "")).strip()
        ]
        likes_out.append(
            {
                "domain": domain,
                "weight": round(float(getattr(d, "weight", 0.0) or 0.0), 2),
                "specifics": specs,
            }
        )
    summary["likes"] = likes_out

    deep_needs = [
        str(n).strip() for n in (getattr(profile, "deep_needs", []) or [])[:5] if str(n).strip()
    ]
    if not deep_needs:
        core = getattr(profile, "core", None)
        if core is not None:
            deep_needs = [
                str(n).strip()
                for n in (getattr(core, "deep_needs", []) or [])[:5]
                if str(n).strip()
            ]
    summary["deep_needs"] = deep_needs

    insights_out: list[dict[str, object]] = []
    for ins in (getattr(profile, "active_insights", []) or [])[:5]:
        hyp = str(getattr(ins, "hypothesis", "")).strip()
        if not hyp:
            continue
        insights_out.append(
            {
                "hypothesis": hyp[:200],
                "confidence": round(float(getattr(ins, "confidence", 0.5) or 0.5), 2),
            }
        )
    summary["active_insights"] = insights_out

    prefs = getattr(profile, "preferences", None)
    summary["exploration_openness"] = round(
        float(getattr(prefs, "exploration_openness", 0.5) or 0.5), 2
    )

    disliked = [
        str(t).strip() for t in (getattr(prefs, "disliked_topics", []) or [])[:8] if str(t).strip()
    ]
    summary["disliked_topics"] = disliked

    return summary


class DelightScorer:
    """Computes a delight score for content based on deep profile resonance.

    The scorer uses embedding similarity to match content against the user's
    deep_needs and active_insights, combined with novelty and quality signals.
    """

    def __init__(
        self,
        embedding_service: SupportsEmbeddingService | None,
        database: SupportsRecommendationSignalStore,
        *,
        weights: DelightWeights | None = None,
        threshold: float = DEFAULT_DELIGHT_THRESHOLD,
    ) -> None:
        self._embedding = embedding_service
        self._database = database
        self._weights = weights or DelightWeights()
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    def effective_threshold(self, exploration_openness: float) -> float:
        """Return a possibly raised threshold for conservative users."""
        if exploration_openness < _LOW_EXPLORATION_OPENNESS:
            return max(self._threshold, CONSERVATIVE_DELIGHT_THRESHOLD)
        return self._threshold

    async def score(
        self,
        candidate: SupportsDelightCandidate,
        profile: Any,
    ) -> tuple[float, DelightSignals, str]:
        """Compute a delight score for a candidate given the soul profile.

        Returns:
            A tuple of (delight_score, signals, reason_stub).
            reason_stub is a short hint for the LLM to expand into the
            full delight_reason.
        """
        w = self._weights
        signals = await self._compute_signals(candidate, profile)

        positive = (
            signals.deep_need_alignment * w.deep_need
            + signals.insight_resonance * w.insight
            + signals.likes_alignment * w.likes
            + signals.novelty_factor * w.novelty
            + signals.quality_indicator * w.quality
            + signals.exploration_match * w.exploration
        )
        penalty = signals.dislike_penalty * w.dislike_penalty
        score = positive - penalty

        reason_stub = self._build_reason_stub(signals, candidate, profile)
        return (min(1.0, max(0.0, score)), signals, reason_stub)

    async def _compute_signals(
        self,
        candidate: SupportsDelightCandidate,
        profile: Any,
    ) -> DelightSignals:
        """Compute individual delight signal components."""
        content_text = f"{candidate.title} {candidate.description or ''}"

        # Probe the embedding subsystem once with the content text. If
        # this returns empty, the provider is genuinely broken — the
        # downstream signal calls would then all return 0.0 silently.
        # Cheap: subsequent embed() calls for the same content_text hit
        # the L1 cache (~10µs).
        embed_alive = True
        if self._embedding is not None:
            probe_vec = await self._embedding.embed(content_text)
            embed_alive = bool(probe_vec)

        deep_need = await self._deep_need_alignment(content_text, profile)
        insight = await self._insight_resonance(content_text, profile)
        likes = await self._likes_alignment(content_text, profile)
        novelty = self._novelty_factor(candidate)
        quality = self._quality_indicator(candidate)
        exploration = self._exploration_match(candidate, profile, novelty)
        dislike = await self._dislike_penalty(content_text, profile)

        # Surface "embedding subsystem dead" cascades — only when the
        # provider actually returned no vector for the content. Earlier
        # version (v0.3.31) flagged the case where all 4 embedding-
        # driven signals were 0.0, but that fires false-positive on
        # legitimate content-out-of-user-interest items: a history doc
        # for a tech-only user gets likes=0.0 from low cosine + clamp,
        # not from a dead embedding. embed_alive directly distinguishes.
        if self._embedding is not None and not embed_alive:
            logger.warning(
                "Delight scoring degraded for %s: embedding provider "
                "returned empty vector for content text "
                "Score will be capped at non-embedding signals only.",
                getattr(candidate, "bvid", "?"),
            )

        return DelightSignals(
            deep_need_alignment=deep_need,
            insight_resonance=insight,
            likes_alignment=likes,
            novelty_factor=novelty,
            quality_indicator=quality,
            exploration_match=exploration,
            dislike_penalty=dislike,
        )

    async def _deep_need_alignment(
        self,
        content_text: str,
        profile: Any,
    ) -> float:
        """Score alignment between content and user's deep needs."""
        if self._embedding is None:
            return 0.0

        deep_needs = getattr(profile, "deep_needs", [])
        if not deep_needs:
            return 0.0

        from openbiliclaw.llm.embedding import cosine_similarity

        content_vec = await self._embedding.embed(content_text)
        if not content_vec:
            return 0.0

        max_sim = 0.0
        for need in deep_needs[:5]:
            need_text = str(need).strip()
            if not need_text:
                continue
            need_vec = await self._embedding.embed(need_text)
            if not need_vec:
                continue
            sim = cosine_similarity(content_vec, need_vec)
            max_sim = max(max_sim, sim)

        # Normalize: similarity 0.5 → 0.0, similarity 1.0 → 1.0
        return max(0.0, min(1.0, (max_sim - 0.5) * 2.0))

    async def _insight_resonance(
        self,
        content_text: str,
        profile: Any,
    ) -> float:
        """Score alignment between content and active insight hypotheses."""
        if self._embedding is None:
            return 0.0

        active_insights = getattr(profile, "active_insights", [])
        if not active_insights:
            return 0.0

        from openbiliclaw.llm.embedding import cosine_similarity

        content_vec = await self._embedding.embed(content_text)
        if not content_vec:
            return 0.0

        max_sim = 0.0
        for insight in active_insights[:5]:
            hypothesis = str(getattr(insight, "hypothesis", "")).strip()
            if not hypothesis:
                continue
            insight_vec = await self._embedding.embed(hypothesis)
            if not insight_vec:
                continue
            sim = cosine_similarity(content_vec, insight_vec)
            # Weight by confidence
            confidence = float(getattr(insight, "confidence", 0.5))
            weighted_sim = sim * (0.5 + confidence * 0.5)
            max_sim = max(max_sim, weighted_sim)

        return max(0.0, min(1.0, (max_sim - 0.4) * 2.5))

    async def _likes_alignment(
        self,
        content_text: str,
        profile: Any,
    ) -> float:
        """Score embedding similarity with the user's actual top likes.

        Uses the onion ``interest.likes`` tree directly so each like's
        text input combines the domain name with its specifics — short
        category words like "游戏" alone produce weak embedding signal
        against B站 titles, but "游戏 / 自走棋 王者荣耀 金铲铲" is
        rich enough to actually correlate with content.
        """
        if self._embedding is None:
            return 0.0

        # Prefer the onion ``interest.likes`` tree (carries specifics).
        # Fall back to flat preferences.interests if the onion shape
        # isn't present.
        like_texts: list[tuple[str, float]] = []  # (text, weight)
        interest_layer = getattr(profile, "interest", None)
        likes = getattr(interest_layer, "likes", []) if interest_layer is not None else []
        for dom in likes[:8]:
            domain = str(getattr(dom, "domain", "")).strip()
            if not domain:
                continue
            spec_names = [
                str(getattr(s, "name", "")).strip()
                for s in getattr(dom, "specifics", [])[:5]
                if str(getattr(s, "name", "")).strip()
            ]
            text = f"{domain}：{' '.join(spec_names)}" if spec_names else domain
            weight = float(getattr(dom, "weight", 0.0) or 0.0)
            like_texts.append((text, weight))

        if not like_texts:
            prefs = getattr(profile, "preferences", None)
            interests = getattr(prefs, "interests", []) if prefs is not None else []
            seen: set[str] = set()
            for tag in interests:
                name = str(getattr(tag, "name", "")).strip()
                weight = float(getattr(tag, "weight", 0.0) or 0.0)
                if not name or name in seen or weight <= 0:
                    continue
                seen.add(name)
                like_texts.append((name, weight))
            like_texts.sort(key=lambda x: x[1], reverse=True)
            like_texts = like_texts[:8]

        if not like_texts:
            return 0.0

        from openbiliclaw.llm.embedding import cosine_similarity

        content_vec = await self._embedding.embed(content_text)
        if not content_vec:
            return 0.0

        max_score = 0.0
        for text, weight in like_texts:
            tag_vec = await self._embedding.embed(text)
            if not tag_vec:
                continue
            sim = cosine_similarity(content_vec, tag_vec)
            # Down-weight low-weight likes — a 0.4-weight tag matters less
            # than a 0.95-weight one.
            score = sim * (0.6 + 0.4 * min(1.0, weight))
            max_score = max(max_score, score)

        # Normalize same as deep_need_alignment: similarity 0.5 → 0.0,
        # 1.0 → 1.0.  Avoids the over-aggressive 2.857 multiplier we
        # tried first which drove typical scores too low.
        return max(0.0, min(1.0, (max_score - 0.5) * 2.0))

    async def _dislike_penalty(
        self,
        content_text: str,
        profile: Any,
    ) -> float:
        """Embedding-based negative signal: how much the content resembles a
        topic the user explicitly disliked.

        Replaces the brittle substring filter at push time — embedding
        similarity catches near-synonyms (e.g. ``手工木工`` matching a
        video about woodworking even when the literal phrase isn't in
        the title) without false-positive collisions on common stems.
        """
        if self._embedding is None:
            return 0.0

        prefs = getattr(profile, "preferences", None)
        disliked = getattr(prefs, "disliked_topics", []) if prefs is not None else []
        # Filter out generic phrases that don't carry a topical signal.
        skip_terms = {"低质内容", "虚假信息", "标题党", "低质", "虚假"}
        topical = [
            str(t).strip() for t in disliked if str(t).strip() and str(t).strip() not in skip_terms
        ]
        if not topical:
            return 0.0

        from openbiliclaw.llm.embedding import cosine_similarity

        content_vec = await self._embedding.embed(content_text)
        if not content_vec:
            return 0.0

        max_sim = 0.0
        for term in topical[:5]:
            term_vec = await self._embedding.embed(term)
            if not term_vec:
                continue
            sim = cosine_similarity(content_vec, term_vec)
            max_sim = max(max_sim, sim)

        # Threshold + amplifier calibrated for bge-m3 (multilingual,
        # the user's main local embedding). bge-m3 puts low-semantic
        # Chinese fragments — live-stream titles like "青梅煮酒_20260425
        # dy主播", short metadata strings, etc. — into a "generic
        # Chinese" embedding cluster where cosine similarity to ANY
        # Chinese phrase floats around 0.78-0.85. The original
        # ``(sim - 0.55) * 2.5`` (calibrated for Gemini's larger
        # baseline spread) blew through this cluster: any low-semantic
        # Chinese title scored dislike_penalty ≈ 0.6-0.73 against
        # arbitrary disliked terms, killing legitimate delight scores.
        # Empirical bge-m3 cosine distribution against the user's
        # disliked_topics:
        #   high-semantic content (e.g. "Scratch物理引擎"):  0.02-0.05
        #   low-semantic fragments ("dy主播 青梅煮酒"):      0.78-0.85
        #   actually similar topic (genuine match):          0.88-0.95
        # Threshold 0.78 cuts the false-positive cluster; amplifier
        # 1.5 keeps the true-positive band (0.88+) actionable without
        # over-penalizing borderline matches.
        return max(0.0, min(1.0, (max_sim - 0.78) * 1.5))

    def _novelty_factor(self, candidate: SupportsDelightCandidate) -> float:
        """Score novelty based on discovery strategy and topic freshness."""
        # Explore strategy inherently carries more novelty
        strategy_novelty = {
            "explore": 0.9,
            "trending": 0.5,
            "related_chain": 0.3,
            "search": 0.2,
        }
        base_novelty = strategy_novelty.get(candidate.source_strategy, 0.3)

        # Check how often this topic has been recommended
        signals = self._database.get_recent_recommendation_signals(limit=30)
        topic = (candidate.topic_group or candidate.topic_key).strip().lower()
        if topic and signals:
            topic_count = sum(
                1 for s in signals if str(s.get("topic_key", "")).strip().lower() == topic
            )
            # Penalize if topic has been seen often
            repetition_penalty = min(1.0, topic_count / 5.0)
            base_novelty = base_novelty * (1.0 - repetition_penalty * 0.5)

        return max(0.0, min(1.0, base_novelty))

    @staticmethod
    def _quality_indicator(candidate: SupportsDelightCandidate) -> float:
        """Score content quality from engagement signals."""
        view_count = max(1, candidate.view_count)
        like_count = candidate.like_count

        if view_count < 100:
            return 0.3  # Not enough data

        like_ratio = like_count / view_count
        # Normalize: 0.01 → 0.2, 0.05 → 0.7, 0.10+ → 1.0
        quality = min(1.0, like_ratio * 12.0)

        # Blend with relevance_score
        return quality * 0.5 + candidate.relevance_score * 0.5

    @staticmethod
    def _exploration_match(
        candidate: SupportsDelightCandidate,
        profile: Any,
        novelty: float,
    ) -> float:
        """Score based on user's exploration openness and content novelty."""
        prefs = getattr(profile, "preferences", None)
        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))

        if exploration_openness > 0.6:
            # Open users delight in novel cross-domain content
            return novelty * exploration_openness
        else:
            # Conservative users delight in deep dives in known domains
            # High relevance in a known domain = deep satisfaction
            depth_signal = candidate.relevance_score * (1.0 - novelty)
            return depth_signal * (1.0 - exploration_openness * 0.5)

    @staticmethod
    def _build_reason_stub(
        signals: DelightSignals,
        candidate: SupportsDelightCandidate,
        profile: Any,
    ) -> str:
        """Build a structured reason stub for LLM expansion."""
        parts: list[str] = []

        if signals.likes_alignment >= 0.6:
            prefs = getattr(profile, "preferences", None)
            interests = getattr(prefs, "interests", []) if prefs is not None else []
            top_like = next(
                (
                    str(getattr(t, "name", "")).strip()
                    for t in interests
                    if str(getattr(t, "name", "")).strip()
                ),
                "",
            )
            if top_like:
                parts.append(f"likes:{top_like}")

        if signals.deep_need_alignment >= 0.6:
            deep_needs = getattr(profile, "deep_needs", [])
            if deep_needs:
                parts.append(f"deep_need:{deep_needs[0]}")

        if signals.insight_resonance >= 0.6:
            insights = getattr(profile, "active_insights", [])
            if insights:
                hypothesis = str(getattr(insights[0], "hypothesis", ""))
                if hypothesis:
                    parts.append(f"insight:{hypothesis[:60]}")

        if signals.novelty_factor >= 0.7:
            parts.append(f"novelty:{candidate.source_strategy}")

        if signals.exploration_match >= 0.7:
            parts.append("exploration:cross_domain")

        if not parts:
            parts.append(f"relevance:{candidate.relevance_score:.2f}")

        return "|".join(parts)
