from __future__ import annotations

import pytest

from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB, FALLBACK_CATEGORY, resolve_category


class _StubEmbed:
    def __init__(self, aliases: dict[str, str], *, fail: bool = False) -> None:
        self._aliases = aliases
        self._fail = fail
        self._vectors: dict[str, list[float]] = {}

    async def embed(self, text: str) -> list[float]:
        if self._fail:
            raise RuntimeError("embedding unavailable")
        key = self._aliases.get(text, text)
        if key not in self._vectors:
            axis = len(self._vectors)
            vec = [0.0] * 32
            vec[axis] = 1.0
            self._vectors[key] = vec
        return self._vectors[key]


@pytest.fixture(autouse=True)
def _clear_vocab_vector_cache() -> None:
    from openbiliclaw.soul import taxonomy

    taxonomy._vocab_vectors.clear()


def test_vocab_bounded_with_fallback() -> None:
    assert len(CATEGORY_VOCAB) <= 20
    assert "其他" in CATEGORY_VOCAB
    assert FALLBACK_CATEGORY == "其他"
    assert len(set(CATEGORY_VOCAB)) == len(CATEGORY_VOCAB)


async def test_resolve_exact_match_passthrough() -> None:
    assert await resolve_category("科技", None) == "科技"
    assert await resolve_category(" 科技 ", None) == "科技"


async def test_resolve_without_embedding_falls_back() -> None:
    assert await resolve_category("内容消费方式", None) == "其他"
    assert await resolve_category("", None) == "其他"


async def test_resolve_nearest_neighbor_with_stub_embedding() -> None:
    assert await resolve_category("技术", _StubEmbed({"技术": "科技"})) == "科技"


async def test_resolve_low_similarity_falls_back() -> None:
    assert await resolve_category("完全无关词", _StubEmbed({})) == "其他"


async def test_resolve_embedding_failure_falls_back() -> None:
    assert await resolve_category("技术", _StubEmbed({}, fail=True)) == "其他"
