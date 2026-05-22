"""Pool purge — recall + LLM precision pipeline.

When a new dislike is learned, the pool is cleaned in two stages:

Stage 1 (hard purge): string exact match on topic_key / topic_group /
    pool_topic_label — high confidence, no LLM needed.

Stage 2 (recall → LLM precision):
  a) Embedding recall with a **low** threshold (0.55) casts a wide net
     over all fresh candidates.
  b) The recalled set is sent to an LLM agent that makes the final
     purge/keep decision per candidate at the intent/pattern level.
     (e.g. "不喜欢营销文" → purge "5分钟教你月入过万")
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database

from openbiliclaw.llm.embedding import cosine_similarity
from openbiliclaw.llm.json_utils import extract_llm_json_object

logger = logging.getLogger(__name__)

# Recall threshold — deliberately low to maximize recall. False positives
# are filtered out by the LLM precision pass. If no LLM is available,
# the old high threshold (0.78) is used as a standalone fallback.
RECALL_THRESHOLD = 0.55
STANDALONE_PURGE_THRESHOLD = 0.78
# Backward compat alias for tests.
DEFAULT_SEMANTIC_PURGE_THRESHOLD = STANDALONE_PURGE_THRESHOLD

# Max candidates to scan per embedding recall pass.
DEFAULT_SCAN_LIMIT = 500


class _SupportsEmbed(Protocol):
    """Minimal protocol — matches EmbeddingService.embed."""

    similarity_threshold: float

    async def embed(self, text: str) -> list[float]: ...


class _SupportsLLMTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        temperature: float = ...,
        max_tokens: int = ...,
        caller: str = ...,
        reasoning_effort: str | None = ...,
    ) -> object: ...


def _candidate_text(cand: dict[str, object]) -> str:
    """Build the text to embed for a candidate."""
    parts: list[str] = []
    for field in ("title", "topic_key", "topic_group", "pool_topic_label"):
        value = str(cand.get(field) or "").strip()
        if value:
            parts.append(value)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Embedding recall
# ---------------------------------------------------------------------------


async def _embedding_recall(
    *,
    candidates: list[dict[str, object]],
    topics: list[str],
    embedding_service: _SupportsEmbed,
    threshold: float,
) -> list[tuple[dict[str, object], float, str]]:
    """Return candidates whose embedding similarity exceeds *threshold*.

    Each result is ``(candidate_row, best_similarity, best_matching_topic)``.
    """
    topic_vectors: list[tuple[str, list[float]]] = []
    for topic in topics:
        try:
            vec = await embedding_service.embed(topic)
        except Exception:
            logger.debug("Failed to embed dislike topic %s", topic, exc_info=True)
            continue
        if vec:
            topic_vectors.append((topic, vec))

    if not topic_vectors:
        return []

    recalled: list[tuple[dict[str, object], float, str]] = []
    for cand in candidates:
        text = _candidate_text(cand)
        if not text:
            continue
        try:
            cand_vec = await embedding_service.embed(text)
        except Exception:
            logger.debug("Failed to embed candidate %s", cand.get("bvid"), exc_info=True)
            continue
        if not cand_vec:
            continue

        best_sim = 0.0
        best_topic = ""
        for topic, topic_vec in topic_vectors:
            sim = cosine_similarity(cand_vec, topic_vec)
            if sim > best_sim:
                best_sim = sim
                best_topic = topic

        if best_sim >= threshold:
            recalled.append((cand, best_sim, best_topic))

    return recalled


# ---------------------------------------------------------------------------
# LLM precision judge
# ---------------------------------------------------------------------------

_LLM_PURGE_SYSTEM_PROMPT = """\
你是内容推荐池的清理 agent。用户新增了一批"不喜欢"标签，\
下面的候选内容已被初步召回为疑似相关。\
你需要逐条判断哪些内容**本质上**属于用户讨厌的类型，即使标题里没有直接出现那个词。

判断标准（按优先级）：
1. **商业意图 / 话术模式**一致：标题党、营销文、带货软文等，即使换了说法也算。
2. **内容类型 / 氛围**一致：例如用户不喜欢"低质鬼畜"，那"沙雕恶搞合集"也算。
3. **受众画像**重叠：如果一个内容的典型受众和用户讨厌的内容高度重合，倾向清除。

不要清除的情况：
- 仅仅是同一大领域但内容风格/深度完全不同（例如用户不喜欢"游戏直播剪辑"，\
但"游戏设计深度分析"不应被清除）。
- 标题模糊、信息不足以判断时，保留（宁可漏杀不可误杀）。

输出严格 JSON，不要附带解释：
{"purge": ["bvid1", "bvid2"], "reason": {"bvid1": "一句话理由", ...}}

如果没有需要清除的，返回 {"purge": [], "reason": {}}"""

_LLM_PURGE_BATCH_SIZE = 30


async def _llm_judge(
    *,
    recalled: list[tuple[dict[str, object], float, str]],
    new_topics: list[str],
    all_topics: list[str],
    llm_service: _SupportsLLMTask,
    batch_size: int = _LLM_PURGE_BATCH_SIZE,
) -> list[str]:
    """Ask LLM to judge recalled candidates. Returns bvids to purge."""
    items = [
        {
            "bvid": str(cand.get("bvid", "")),
            "title": str(cand.get("title", "")),
            "topic_group": str(cand.get("topic_group", "")),
            "topic_label": str(cand.get("pool_topic_label", "")),
            "recall_similarity": round(sim, 2),
            "matched_dislike": matched_topic,
        }
        for cand, sim, matched_topic in recalled
        if str(cand.get("bvid", "")).strip()
    ]
    if not items:
        return []

    bvids_to_purge: list[str] = []
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start : batch_start + batch_size]

        user_input = json.dumps(
            {
                "newly_added_dislikes": new_topics,
                "all_disliked_topics": all_topics,
                "candidates": batch,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        try:
            response = await llm_service.complete_structured_task(
                system_instruction=_LLM_PURGE_SYSTEM_PROMPT,
                user_input=user_input,
                temperature=0.1,
                max_tokens=2048,
                caller="pool_purge.llm_agent",
                reasoning_effort="",
            )
        except Exception:
            logger.debug("LLM purge batch failed", exc_info=True)
            continue

        raw = str(getattr(response, "content", "")).strip()
        parsed = extract_llm_json_object(raw)
        if not isinstance(parsed, dict):
            logger.debug("LLM purge: unparseable response: %.200s", raw)
            continue

        purge_list = parsed.get("purge", [])
        reasons = parsed.get("reason", {})
        if not isinstance(purge_list, list) or not purge_list:
            continue

        for bvid_raw in purge_list:
            bvid = str(bvid_raw).strip()
            if bvid:
                reason = reasons.get(bvid, "") if isinstance(reasons, dict) else ""
                logger.info("LLM purge: '%s' — %s", bvid, reason)
                bvids_to_purge.append(bvid)

    return bvids_to_purge


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def semantic_purge_pool_by_disliked_topics(
    *,
    database: Database,
    topics: list[str],
    embedding_service: _SupportsEmbed,
    threshold: float | None = None,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> int:
    """Standalone embedding purge (no LLM available).

    Uses the high standalone threshold (0.78) to avoid false positives.
    Called when ``llm_service`` is not available — the recall+LLM path
    is preferred when both services are present.
    """
    clean_topics = [t.strip() for t in topics if t and t.strip()]
    if not clean_topics or embedding_service is None:
        return 0

    effective_threshold = threshold if threshold is not None else STANDALONE_PURGE_THRESHOLD

    candidates = database.get_fresh_pool_candidates_for_purge_scan(limit=scan_limit)
    if not candidates:
        return 0

    recalled = await _embedding_recall(
        candidates=candidates,
        topics=clean_topics,
        embedding_service=embedding_service,
        threshold=effective_threshold,
    )

    bvids_to_purge = [
        str(cand.get("bvid", "")).strip()
        for cand, _sim, _topic in recalled
        if str(cand.get("bvid", "")).strip()
    ]
    for cand, sim, topic in recalled:
        logger.info(
            "Semantic purge: '%s' (%.2f ~ '%s')",
            str(cand.get("title", ""))[:40],
            sim,
            topic,
        )

    if not bvids_to_purge:
        return 0
    return database.mark_pool_items_purged_by_dislike(bvids_to_purge)


async def recall_and_llm_purge_pool(
    *,
    database: Database,
    topics: list[str],
    all_disliked_topics: list[str],
    embedding_service: _SupportsEmbed,
    llm_service: _SupportsLLMTask,
    recall_threshold: float = RECALL_THRESHOLD,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> int:
    """Two-stage purge: embedding recall (low threshold) → LLM precision.

    Args:
        database: Database handle.
        topics: Newly added dislike topics.
        all_disliked_topics: Full dislike list for LLM context.
        embedding_service: Embedding service for recall.
        llm_service: LLM service for precision judgment.
        recall_threshold: Similarity threshold for recall (default 0.55).
        scan_limit: Max candidates for embedding scan.

    Returns:
        Number of candidates purged.
    """
    clean_new = [t.strip() for t in topics if t and t.strip()]
    clean_all = [t.strip() for t in all_disliked_topics if t and t.strip()]
    if not clean_new:
        return 0

    candidates = database.get_fresh_pool_candidates_for_purge_scan(limit=scan_limit)
    if not candidates:
        return 0

    # Stage 1: wide embedding recall
    recalled = await _embedding_recall(
        candidates=candidates,
        topics=clean_new,
        embedding_service=embedding_service,
        threshold=recall_threshold,
    )
    if not recalled:
        return 0

    logger.info(
        "Dislike recall: %d/%d candidates above %.2f threshold",
        len(recalled),
        len(candidates),
        recall_threshold,
    )

    # Stage 2: LLM precision judge
    bvids_to_purge = await _llm_judge(
        recalled=recalled,
        new_topics=clean_new,
        all_topics=clean_all,
        llm_service=llm_service,
    )
    if not bvids_to_purge:
        return 0

    return database.mark_pool_items_purged_by_dislike(bvids_to_purge)
