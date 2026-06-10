"""Embedding service with two-layer caching for semantic similarity.

Provides text embedding via configurable models (default: Gemini),
with L1 in-memory cache and L2 SQLite persistent cache.
Discovery writes embeddings to L2; recommendation reads from L2
with zero API calls on the hot path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class SupportsEmbed(Protocol):
    """Protocol for providers that support text embedding."""

    async def embed(self, text: str, *, model: str = ...) -> list[float]: ...


class SupportsEmbeddingService(Protocol):
    """Protocol for semantic embedding helpers used by mainline services."""

    similarity_threshold: float

    async def embed(self, text: str) -> list[float]: ...

    def lookup_cached(self, text: str) -> list[float]:
        """Cache-only lookup; default returns ``[]`` for protocol compatibility."""
        return []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (pure Python)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingCache:
    """SQLite-backed persistent embedding cache (L2).

    Stores text → vector mappings in a dedicated table so embeddings
    computed during discovery survive process restarts and are reusable
    during recommendation serving without any API calls.

    Thread-safe: the cache is read/written from background discovery and
    recommendation-prewarm workers running on different threads, so the single
    connection is opened with ``check_same_thread=False`` and every access is
    serialized by an ``RLock`` (a bare ``sqlite3`` connection otherwise raises
    "SQLite objects created in a thread can only be used in that same thread").
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._conn = sqlite3.connect(str(self._db_path), timeout=10.0, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()
            self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("EmbeddingCache not initialized")
        return self._conn

    def _ensure_schema(self) -> None:
        table_exists = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'embedding_cache'"
        ).fetchone()
        if table_exists is None:
            self._create_cache_table()
            return

        columns = self.conn.execute("PRAGMA table_info(embedding_cache)").fetchall()
        column_names = {str(row[1]) for row in columns}
        pk_columns = [
            str(row[1]) for row in sorted(columns, key=lambda row: int(row[5] or 0)) if row[5]
        ]
        if column_names >= {"text_key", "vector", "model"} and pk_columns == ["text_key", "model"]:
            return

        self.conn.execute("ALTER TABLE embedding_cache RENAME TO embedding_cache_legacy")
        self._create_cache_table()
        legacy_columns = {str(row[1]) for row in columns}
        if {"text_key", "vector"} <= legacy_columns:
            model_expr = "COALESCE(model, '')" if "model" in legacy_columns else "''"
            self.conn.execute(
                f"""INSERT OR REPLACE INTO embedding_cache (text_key, model, vector)
                    SELECT text_key, {model_expr}, vector
                    FROM embedding_cache_legacy"""
            )
        self.conn.execute("DROP TABLE embedding_cache_legacy")

    def _create_cache_table(self) -> None:
        self.conn.execute(
            """CREATE TABLE embedding_cache (
                text_key TEXT NOT NULL,
                model    TEXT NOT NULL DEFAULT '',
                vector   TEXT NOT NULL,
                PRIMARY KEY (text_key, model)
            )"""
        )

    def get(self, key: str, model: str = "") -> list[float] | None:
        with self._lock:
            if model:
                row = self.conn.execute(
                    "SELECT vector FROM embedding_cache WHERE text_key = ? AND model = ?",
                    (key, model),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT vector FROM embedding_cache WHERE text_key = ? ORDER BY model LIMIT 1",
                    (key,),
                ).fetchone()
        if row is None:
            return None
        try:
            return _coerce_embedding_vector(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            return None

    def put(self, key: str, vector: list[float], model: str = "") -> None:
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO embedding_cache (text_key, vector, model)
                   VALUES (?, ?, ?)""",
                (key, json.dumps(vector), model),
            )
            self.conn.commit()

    def count(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
        return row[0] if row else 0


class EmbeddingService:
    """Cached embedding service for semantic similarity operations.

    Two-layer cache:
    - L1: in-memory dict (fastest, session-scoped)
    - L2: SQLite persistent cache (survives restarts)

    Discovery writes to both layers; recommendation reads hit L1 first,
    then L2, and only calls the API as a last resort.

    All parameters (model, threshold, cache_size) can be configured
    via ``[llm.embedding]`` in config.toml.
    """

    # Fixed text used by ``probe()`` for /api/health live readiness checks.
    _PROBE_TEXT = "openbiliclaw embedding readiness probe"

    def __init__(
        self,
        provider: SupportsEmbed,
        *,
        model: str = "gemini-embedding-001",
        cache_model: str | None = None,
        cache_size: int = 500,
        similarity_threshold: float = 0.82,
        persistent_cache: EmbeddingCache | None = None,
        max_concurrent_provider_calls: int = 2,
    ) -> None:
        self._provider = provider
        self._model = model
        self._cache_model = cache_model or model
        # OrderedDict + move_to_end on hit gives us proper LRU instead of
        # FIFO. With a 500-key cache and bursty access patterns (delight
        # scoring iterates the same like_texts repeatedly), FIFO would
        # evict heavy-hit keys whenever the cache filled with cold misses.
        self._l1_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_size = cache_size
        self.similarity_threshold = similarity_threshold
        self._l2_cache = persistent_cache
        # Cap concurrent provider calls. Local CPU-bound providers (Ollama
        # bge-m3 on a single GGUF runner) collapse under unbounded
        # asyncio.gather fan-out from delight scoring + topic supergroup
        # merge + speculator. v0.3.31 caught a real cascade where the
        # daemon spawned 14+ concurrent embed calls within 1 second after
        # the proxy fix landed; Ollama queued them serially, exceeded the
        # 60s read timeout, and every call returned ``[]``. Even cloud
        # providers benefit from a small ceiling to amortize TLS handshake
        # cost. Default 2 keeps single-CPU bge-m3 healthy while still
        # using both cores for inference + tokenization.
        self._provider_semaphore = asyncio.Semaphore(max_concurrent_provider_calls)

    def lookup_cached(self, text: str) -> list[float]:
        """Cache-only lookup — never triggers a provider API call.

        Returns ``[]`` on miss. Callers (recommendation hot path) use
        this when they need a hard latency budget: a miss means the
        item simply doesn't participate in embedding-based diversity
        for this batch, and the warmer task fills the cache asynchronously
        for subsequent batches.
        """
        key = text.strip().lower()[:200]
        if not key:
            return []
        cached = self._l1_cache.get(key)
        if cached is not None:
            self._l1_cache.move_to_end(key)
            return cached
        if self._l2_cache is not None:
            persisted = self._l2_cache.get(key, model=self._cache_model)
            if persisted is not None:
                self._l1_cache[key] = persisted
                return persisted
        return []

    async def embed(self, text: str) -> list[float]:
        """Get embedding for text. Checks L1 → L2 → API."""
        key = text.strip().lower()[:200]
        if not key:
            return []

        # L1 / L2 cache lookup (also covers warming-side hits).
        cached = self.lookup_cached(text)
        if cached:
            return cached

        # L3: API call (throttled — see __init__ semaphore comment)
        async with self._provider_semaphore:
            try:
                vector = await self._provider.embed(key, model=self._model)
            except Exception:
                logger.warning("Embedding failed for: %s", key[:50], exc_info=True)
                return []

        # Never cache an empty vector. Empty means the provider failed
        # transparently (e.g. swallowed timeout) and returned ``[]``;
        # caching that pins the text to "no embedding" forever even
        # after the upstream issue is fixed. v0.3.31 had ~170 keys
        # poisoned this way before this guard existed — top user
        # interests like 游戏攻略 / 洛克王国 / 金铲铲之战 were affected
        # and the cascade silently zero'd DelightScorer's
        # likes_alignment for the most relevant content. Surface a
        # WARN per occurrence so the failure mode is visible at the
        # service layer, not buried in provider-level logs.
        if not vector:
            logger.warning(
                "Embedding service got empty vector for key=%r — "
                "provider returned [] (likely transient failure). "
                "Skipping cache write so the next call retries.",
                key[:80],
            )
            return []

        # Store in both caches (LRU eviction: popitem(last=False) drops
        # the least-recently-used entry — combined with move_to_end on
        # cache hit above, this is true LRU instead of FIFO).
        if len(self._l1_cache) >= self._cache_size:
            self._l1_cache.popitem(last=False)
        self._l1_cache[key] = vector

        if self._l2_cache is not None:
            try:
                self._l2_cache.put(key, vector, model=self._cache_model)
            except Exception:
                logger.debug("L2 cache write failed", exc_info=True)

        return vector

    async def probe(self) -> bool:
        """Live readiness check — bypasses the cache and hits the provider once.

        Returns ``True`` only when the provider currently returns a
        non-empty vector. The L1/L2 cache is bypassed on purpose: a
        previously-cached success must never mask a provider that has
        since gone down (Ollama stopped, ``bge-m3`` never pulled so every
        call 404s, remote key revoked, …). ``/api/health`` calls this
        behind its own short TTL + single-flight, so the extra provider
        round-trip happens at most a couple of times a minute.
        """
        async with self._provider_semaphore:
            try:
                vector = await self._provider.embed(self._PROBE_TEXT, model=self._model)
            except Exception:
                logger.debug("Embedding readiness probe failed", exc_info=True)
                return False
        return bool(vector)

    async def are_similar(self, text_a: str, text_b: str) -> bool:
        """Check if two texts are semantically similar above threshold."""
        vec_a = await self.embed(text_a)
        vec_b = await self.embed(text_b)
        if not vec_a or not vec_b:
            return False
        return cosine_similarity(vec_a, vec_b) >= self.similarity_threshold

    async def find_similar_cluster(
        self,
        text: str,
        existing_clusters: dict[str, list[float]],
    ) -> str | None:
        """Find which existing cluster a text belongs to, or None if novel.

        Args:
            text: The text to classify.
            existing_clusters: Map of cluster_label → centroid_vector.

        Returns:
            The label of the most similar cluster (if above threshold), or None.
        """
        vec = await self.embed(text)
        if not vec:
            return None
        best_label: str | None = None
        best_sim = 0.0
        for label, centroid in existing_clusters.items():
            sim = cosine_similarity(vec, centroid)
            if sim > best_sim:
                best_sim = sim
                best_label = label
        if best_sim >= self.similarity_threshold:
            return best_label
        return None

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._l1_cache.clear()


def _coerce_embedding_vector(value: object) -> list[float] | None:
    if not isinstance(value, list):
        return None
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        vector.append(float(item))
    return vector
