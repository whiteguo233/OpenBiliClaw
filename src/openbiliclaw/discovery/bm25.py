"""BM25 sparse-lexical retrieval for the learned relevance scorer.

Complements the dense embedding scorer with a sparse lexical signal:
CJK text is tokenised as character bigrams, Latin text as lowercased
word tokens. Pure-Python, deterministic, no third-party dependencies.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Latin words and CJK runs, matched left-to-right; everything else is dropped.
_SEGMENT_RE = re.compile(r"[a-z0-9]+|[一-鿿]+")


def cjk_tokenize(text: str) -> list[str]:
    """Tokenise ``text`` into CJK char bigrams and lowercased Latin words.

    Consecutive CJK run ``abcd`` -> ``ab bc cd``; Latin run -> one word per
    ``[a-z0-9]+``; punctuation and whitespace are dropped.
    """
    tokens: list[str] = []
    for segment in _SEGMENT_RE.findall(text.lower()):
        if segment.isascii() or len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    return tokens


class BM25Index:
    """In-memory BM25 index over tokenised documents."""

    def __init__(
        self,
        docs: Sequence[Sequence[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._k1 = k1
        self._b = b
        self._doc_len = [len(doc) for doc in docs]
        n = len(docs)
        self._avgdl = sum(self._doc_len) / n if n else 0.0

        df: dict[str, int] = {}
        for doc in docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1
        # 非负 idf（BM25+ 变体）：小批量里高频词可能超过半数文档，避免负权重。
        self._idf = {
            term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

        self._tf: list[dict[str, int]] = []
        for doc in docs:
            counts: dict[str, int] = {}
            for term in doc:
                counts[term] = counts.get(term, 0) + 1
            self._tf.append(counts)

    def score(self, query_tokens: Sequence[str], doc_idx: int) -> float:
        """Return the BM25 relevance score of ``docs[doc_idx]`` for ``query_tokens``."""
        if doc_idx < 0 or doc_idx >= len(self._tf):
            return 0.0
        tf = self._tf[doc_idx]
        doc_len = self._doc_len[doc_idx]
        # 空文档 / 空平均长度时退化为无长度归一化，避免除零。
        length_ratio = doc_len / self._avgdl if self._avgdl > 0.0 else 1.0
        total = 0.0
        for term in query_tokens:
            idf = self._idf.get(term)
            if idf is None:
                continue
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            denominator = freq + self._k1 * (1.0 - self._b + self._b * length_ratio)
            total += idf * (freq * (self._k1 + 1.0)) / denominator
        return total
