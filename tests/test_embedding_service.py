"""Tests for embedding cache and service helpers."""

from pathlib import Path

from openbiliclaw.llm.embedding import EmbeddingCache, EmbeddingService


class _FakeEmbedProvider:
    """Minimal ``SupportsEmbed`` double with controllable behaviour."""

    def __init__(
        self, *, vector: list[float] | None = None, error: Exception | None = None
    ) -> None:
        self._vector = [0.1, 0.2, 0.3] if vector is None else vector
        self._error = error
        self.calls: list[str] = []

    async def embed(self, text: str, *, model: str = "") -> list[float]:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return list(self._vector)


async def test_probe_true_when_provider_returns_vector() -> None:
    provider = _FakeEmbedProvider(vector=[0.1, 0.2])
    service = EmbeddingService(provider, model="bge-m3")

    assert await service.probe() is True
    assert provider.calls  # the provider was actually hit


async def test_probe_false_when_provider_returns_empty() -> None:
    # Empty vector = transient/upstream failure (e.g. bge-m3 not pulled).
    provider = _FakeEmbedProvider(vector=[])
    service = EmbeddingService(provider, model="bge-m3")

    assert await service.probe() is False


async def test_probe_false_when_provider_raises() -> None:
    provider = _FakeEmbedProvider(error=RuntimeError("404 Not Found"))
    service = EmbeddingService(provider, model="bge-m3")

    assert await service.probe() is False


async def test_probe_bypasses_cache_and_hits_provider_each_call() -> None:
    # A cached success must never mask a provider that later goes down, so
    # probe() issues a real provider call instead of reading the cache.
    provider = _FakeEmbedProvider(vector=[0.5, 0.5])
    service = EmbeddingService(provider, model="bge-m3")

    await service.probe()
    await service.probe()

    assert len(provider.calls) == 2


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


def test_embedding_cache_is_thread_safe_across_threads(tmp_path: Path) -> None:
    # Regression: discovery candidate post-processing and recommendation prewarm
    # touch the cache from worker threads other than the one that opened it. A
    # bare sqlite3 connection (check_same_thread=True) raises "SQLite objects
    # created in a thread can only be used in that same thread".
    import threading

    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()  # connection opened on this (main) thread

    errors: list[Exception] = []
    results: dict[str, object] = {}

    def worker() -> None:
        try:
            cache.put("k", [0.1, 0.2, 0.3], model="bge-m3")
            results["get"] = cache.get("k")
            results["count"] = cache.count()
        except Exception as exc:  # noqa: BLE001 — capture for assertion
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert errors == [], f"cache raised across threads: {errors}"
    assert results["get"] == [0.1, 0.2, 0.3]
    assert results["count"] == 1
