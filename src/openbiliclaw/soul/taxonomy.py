"""Closed category vocabulary for the first level of the interest tree."""

from __future__ import annotations

from typing import Protocol

CATEGORY_VOCAB: tuple[str, ...] = (
    "娱乐",
    "生活",
    "科技",
    "知识",
    "游戏",
    "资讯",
    "体育",
    "健康",
    "社会",
    "音乐",
    "动漫",
    "财经",
    "影视",
    "美食",
    "教育",
    "文化",
    "萌宠",
    "汽车",
    "其他",
)
FALLBACK_CATEGORY = "其他"

# Category resolution is semantic routing, not duplicate detection. Keep the
# threshold below the consolidator's near-duplicate threshold, but fall back
# instead of force-fitting unrelated labels.
_NN_SIMILARITY_THRESHOLD = 0.55


class SupportsEmbed(Protocol):
    async def embed(self, text: str) -> list[float]: ...


_vocab_vectors: dict[str, list[float]] = {}


async def resolve_category(raw: str, embed: SupportsEmbed | None = None) -> str:
    """Resolve an arbitrary category string into ``CATEGORY_VOCAB``."""
    name = str(raw or "").strip()
    if name in CATEGORY_VOCAB:
        return name
    if not name or embed is None:
        return FALLBACK_CATEGORY

    try:
        from openbiliclaw.llm.embedding import cosine_similarity

        raw_vec = await embed.embed(name)
        if not raw_vec:
            return FALLBACK_CATEGORY

        best = FALLBACK_CATEGORY
        best_sim = 0.0
        for term in CATEGORY_VOCAB:
            if term == FALLBACK_CATEGORY:
                continue
            vec = _vocab_vectors.get(term)
            if vec is None:
                vec = await embed.embed(term)
                if vec:
                    _vocab_vectors[term] = vec
            if not vec:
                continue
            sim = cosine_similarity(raw_vec, vec)
            if sim > best_sim:
                best = term
                best_sim = sim
        return best if best_sim >= _NN_SIMILARITY_THRESHOLD else FALLBACK_CATEGORY
    except Exception:
        return FALLBACK_CATEGORY
