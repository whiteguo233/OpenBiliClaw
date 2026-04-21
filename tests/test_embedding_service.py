"""Tests for embedding cache and service helpers."""

from pathlib import Path

from openbiliclaw.llm.embedding import EmbeddingCache


def test_embedding_cache_get_rejects_non_list_payload(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, vector, model) VALUES (?, ?, ?)",
        ("bad-object", '{"oops": 1}', ""),
    )
    cache.conn.commit()

    assert cache.get("bad-object") is None


def test_embedding_cache_get_rejects_non_numeric_vectors(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, vector, model) VALUES (?, ?, ?)",
        ("bad-vector", '[1, "oops", 3]', ""),
    )
    cache.conn.commit()

    assert cache.get("bad-vector") is None
