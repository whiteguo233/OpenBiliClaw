"""Semantic pool purge — embedding-based dislike matching.

When a new dislike is learned, string matching catches the obvious cases
(topic_key / title substring). This module provides the second layer:
embedding-based semantic matching that catches candidates whose title does
not literally contain the dislike word but is semantically close to it.

Example: user says "不喜欢鬼畜", pool has a video titled "恶搞鬼畜区经典回顾"
— string match catches this via "鬼畜" substring. But a video titled
"沙雕视频合集" without the word "鬼畜" would only be caught by semantic match.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database

from openbiliclaw.llm.embedding import cosine_similarity

logger = logging.getLogger(__name__)

# Default semantic similarity threshold for purging. Deliberately higher than
# the embedding service's default `similarity_threshold` (0.82) so we only
# purge on strong semantic matches, not weak ones — false positives here
# directly hurt recommendation recall.
DEFAULT_SEMANTIC_PURGE_THRESHOLD = 0.78

# Max candidates to scan per purge pass. Prevents unbounded embedding calls
# when the pool is very large.
DEFAULT_SCAN_LIMIT = 200


class _SupportsEmbed(Protocol):
    """Minimal protocol — matches EmbeddingService.embed."""

    similarity_threshold: float

    async def embed(self, text: str) -> list[float]: ...


async def semantic_purge_pool_by_disliked_topics(
    *,
    database: Database,
    topics: list[str],
    embedding_service: _SupportsEmbed,
    threshold: float | None = None,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> int:
    """Purge pool candidates whose title/topic is semantically close to a dislike.

    Pipeline:
      1. Fetch fresh, not-yet-recommended pool candidates (bounded by scan_limit)
      2. Compute embeddings for each new dislike topic (cached after first call)
      3. For each candidate, embed ``title`` and ``topic_key`` (also cached)
      4. If max(similarity_to_any_dislike) >= threshold, mark for purge
      5. Bulk-update the marked bvids to ``pool_status = 'purged_by_dislike'``

    Silent no-op if any of: topics empty, embedding_service missing, or
    any embedding call returns empty.

    Returns:
        Number of candidates purged (may be 0 if no semantic matches found).
    """
    clean_topics = [t.strip() for t in topics if t and t.strip()]
    if not clean_topics or embedding_service is None:
        return 0

    effective_threshold = (
        threshold
        if threshold is not None
        else DEFAULT_SEMANTIC_PURGE_THRESHOLD
    )

    candidates = database.get_fresh_pool_candidates_for_purge_scan(limit=scan_limit)
    if not candidates:
        return 0

    # 1. Embed dislike topics once
    topic_vectors: list[tuple[str, list[float]]] = []
    for topic in clean_topics:
        try:
            vec = await embedding_service.embed(topic)
        except Exception:
            logger.debug("Failed to embed dislike topic %s", topic, exc_info=True)
            continue
        if vec:
            topic_vectors.append((topic, vec))

    if not topic_vectors:
        return 0

    # 2. Scan candidates
    bvids_to_purge: list[str] = []
    for cand in candidates:
        # Build the text we'll embed for this candidate. Prefer the most
        # information-dense concatenation of title + topic fields.
        candidate_text_parts: list[str] = []
        for field in ("title", "topic_key", "topic_group", "pool_topic_label"):
            value = str(cand.get(field) or "").strip()
            if value:
                candidate_text_parts.append(value)
        if not candidate_text_parts:
            continue
        candidate_text = " ".join(candidate_text_parts)

        try:
            cand_vec = await embedding_service.embed(candidate_text)
        except Exception:
            logger.debug(
                "Failed to embed candidate %s", cand.get("bvid"), exc_info=True,
            )
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

        if best_sim >= effective_threshold:
            logger.info(
                "Semantic purge: '%s' (%.2f ~ '%s')",
                cand.get("title", "")[:40], best_sim, best_topic,
            )
            bvid = str(cand.get("bvid", "")).strip()
            if bvid:
                bvids_to_purge.append(bvid)

    if not bvids_to_purge:
        return 0

    return database.mark_pool_items_purged_by_dislike(bvids_to_purge)
