"""Tests for embedding cache and service helpers."""

import sqlite3
from pathlib import Path

import pytest

from openbiliclaw.llm.embedding import (
    EmbeddingCache,
    EmbeddingService,
    build_embedding_provenance,
    cosine_similarity,
    decode_embedding_vector_blob,
    decode_embedding_vector_payload,
    encode_embedding_vector_blob,
    image_embedding_cache_key,
    normalize_embedding_endpoint,
    split_l2_model_namespace,
)
from openbiliclaw.llm.gemini_provider import GeminiProvider


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


class _ScriptedEmbedProvider:
    """``SupportsEmbed`` double that plays a per-call success/failure script."""

    def __init__(self, script: list[list[float] | Exception]) -> None:
        self._script = script
        self.calls: list[str] = []

    async def embed(self, text: str, *, model: str = "") -> list[float]:
        self.calls.append(text)
        result = self._script.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_embedding_endpoint_normalization_redacts_secret_url_parts() -> None:
    endpoint = "HTTPS://user:api-secret@Relay.Example:443/v1/?token=secret#fragment"

    assert normalize_embedding_endpoint(endpoint) == "https://relay.example/v1"
    assert "secret" not in normalize_embedding_endpoint(endpoint)
    assert normalize_embedding_endpoint("user:secret@relay.example/v1?token=secret") == (
        "relay.example/v1"
    )


async def test_embedding_l2_namespace_isolated_by_endpoint_and_stable(
    tmp_path: Path,
) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    common = {
        "logical_provider": "openai_compatible",
        "model": "embed-v1",
        "output_dimensionality": 3,
        "persistent_cache": cache,
    }
    first_provider = _FakeEmbedProvider(vector=[0.1, 0.2, 0.3])
    second_provider = _FakeEmbedProvider(vector=[0.7, 0.8, 0.9])
    first = EmbeddingService(
        first_provider,
        endpoint="HTTPS://user:secret@Relay.Example:443/v1/?token=secret#fragment",
        **common,
    )
    second = EmbeddingService(
        second_provider,
        endpoint="https://other.example/v1",
        **common,
    )
    same_config = EmbeddingService(
        _FakeEmbedProvider(),
        endpoint="https://relay.example/v1/",
        **common,
    )

    assert first.embedding_fingerprint != second.embedding_fingerprint
    assert first.cache_model_namespace != second.cache_model_namespace
    assert first.embedding_fingerprint == same_config.embedding_fingerprint
    assert first.cache_model_namespace == same_config.cache_model_namespace
    assert "secret" not in first.cache_model_namespace

    assert await first.embed("same text") == [0.1, 0.2, 0.3]
    assert second.lookup_cached("same text") == []
    assert await second.embed("same text") == [0.7, 0.8, 0.9]
    assert len(first_provider.calls) == 1
    assert len(second_provider.calls) == 1
    # A fresh service with the same normalized provenance can reuse the L2
    # row, which pins cross-process stability without sharing other endpoints.
    # L2 rows are stored as float32 blobs, so the round-trip value is only
    # approx-equal to the original float64 vector (see precision test below).
    assert await same_config.embed("same text") == pytest.approx([0.1, 0.2, 0.3], rel=1e-6)


def test_embedding_provenance_isolated_by_provider_model_and_dimension() -> None:
    base = build_embedding_provenance("openai", "https://relay.example/v1", "embed-v1", 3)
    other_provider = build_embedding_provenance("gemini", "https://relay.example/v1", "embed-v1", 3)
    other_model = build_embedding_provenance("openai", "https://relay.example/v1", "embed-v2", 3)
    other_dimension = build_embedding_provenance(
        "openai", "https://relay.example/v1", "embed-v1", 4
    )

    assert len({base, other_provider, other_model, other_dimension}) == 4


class _FakeImageEmbedProvider(_FakeEmbedProvider):
    """Text + image embedding double for multimodal path tests."""

    supports_image_embedding = True

    def __init__(
        self,
        *,
        vector: list[float] | None = None,
        image_vector: list[float] | None = None,
        error: Exception | None = None,
        image_error: Exception | None = None,
    ) -> None:
        super().__init__(vector=vector, error=error)
        self._image_vector = [0.9, 0.1, 0.0] if image_vector is None else image_vector
        self._image_error = image_error
        self.image_calls: list[tuple[int, str, str]] = []

    @staticmethod
    def is_multimodal_embedding_model(model: str) -> bool:
        return "embedding-2" in (model or "").lower()

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        model: str = "",
    ) -> list[float]:
        self.image_calls.append((len(image_bytes), mime_type, model))
        if self._image_error is not None:
            raise self._image_error
        return list(self._image_vector)


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


def test_embedding_cache_close_is_idempotent(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()

    cache.close()
    cache.close()

    with pytest.raises(RuntimeError, match="not initialized"):
        _ = cache.conn


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
    # L2 round-trip is float32-quantized, so compare approx-equal.
    assert results["get"] == pytest.approx([0.1, 0.2, 0.3], rel=1e-6)
    assert results["count"] == 1


def test_gemini_multimodal_embedding_model_detection() -> None:
    assert GeminiProvider.is_multimodal_embedding_model("gemini-embedding-2")
    assert GeminiProvider.is_multimodal_embedding_model("gemini-embedding-2-preview")
    assert not GeminiProvider.is_multimodal_embedding_model("gemini-embedding-001")
    assert not GeminiProvider.is_multimodal_embedding_model("bge-m3")
    assert not GeminiProvider.is_multimodal_embedding_model("")


async def test_embed_image_inactive_when_multimodal_disabled() -> None:
    provider = _FakeImageEmbedProvider()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        multimodal_enabled=False,
    )

    assert service.supports_image_embedding is True
    assert service.image_embedding_active() is False
    assert await service.embed_image(b"fake-jpeg-bytes") == []
    assert provider.image_calls == []


async def test_embed_image_inactive_for_text_only_model() -> None:
    provider = _FakeImageEmbedProvider()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-001",
        multimodal_enabled=True,
    )

    assert service.supports_image_embedding is False
    assert await service.embed_image(b"fake-jpeg-bytes") == []
    assert provider.image_calls == []


async def test_embed_image_caches_and_reuses_vector(tmp_path: Path) -> None:
    provider = _FakeImageEmbedProvider(image_vector=[0.2, 0.4, 0.6])
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        cache_model="gemini-embedding-2#dim=1024",
        persistent_cache=cache,
        multimodal_enabled=True,
    )
    image = b"\xff\xd8\xff" + b"cover-bytes-demo"

    first = await service.embed_image(image, mime_type="image/jpeg")
    second = await service.embed_image(image, mime_type="image/jpeg")

    assert first == [0.2, 0.4, 0.6]
    assert second == first
    assert len(provider.image_calls) == 1
    key = image_embedding_cache_key(image)
    assert service.lookup_cached_image(key) == first
    # L2 rows are stored as float32 blobs; exact float64 equality is not
    # guaranteed across the persistence round-trip.
    assert cache.get(key, model="gemini-embedding-2#dim=1024") == pytest.approx(first, rel=1e-6)


async def test_embed_image_skips_cache_on_empty_vector() -> None:
    provider = _FakeImageEmbedProvider(image_vector=[])
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        multimodal_enabled=True,
    )
    image = b"empty-result"

    assert await service.embed_image(image) == []
    assert await service.embed_image(image) == []
    assert len(provider.image_calls) == 2


async def test_text_only_provider_has_no_image_support() -> None:
    provider = _FakeEmbedProvider()
    service = EmbeddingService(
        provider,
        model="bge-m3",
        multimodal_enabled=True,
    )
    assert service.supports_image_embedding is False
    assert service.image_embedding_active() is False


async def test_document_embedding_does_not_collide_on_shared_200_char_prefix() -> None:
    provider = _FakeEmbedProvider()
    service = EmbeddingService(provider, model="bge-m3")
    first = "a" * 200 + "-first-document"
    second = "a" * 200 + "-second-document"

    await service.embed_document(first)
    await service.embed_document(second)

    assert len(provider.calls) == 2
    assert service.lookup_cached_document(first)
    assert service.lookup_cached_document(second)


# ---------------------------------------------------------------------------
# Versioned BLOB encoding (issue #153: JSON vector bloat)
# ---------------------------------------------------------------------------


def test_blob_vector_roundtrip_preserves_dimension_and_approx_values() -> None:
    vector = [0.1, 0.2, -0.3, 1.0, 0.0]
    blob = encode_embedding_vector_blob(vector)

    assert isinstance(blob, bytes)
    assert len(blob) == 12 + 5 * 4  # header + float32 payload
    decoded = decode_embedding_vector_blob(blob)
    assert decoded is not None
    assert len(decoded) == 5
    assert decoded == pytest.approx(vector, rel=1e-6)


def test_blob_vector_4096_dimensions_stays_compact() -> None:
    vector = [float(i % 17) * 0.01 for i in range(4096)]
    blob = encode_embedding_vector_blob(vector)

    # ~16 KiB per row instead of ~90 KiB of JSON text.
    assert len(blob) == 12 + 4096 * 4
    decoded = decode_embedding_vector_blob(blob)
    assert decoded is not None
    assert len(decoded) == 4096
    assert cosine_similarity(vector, decoded) >= 0.9999


def test_blob_vector_rejects_malformed_payloads() -> None:
    vector = [0.1, 0.2, 0.3]
    blob = encode_embedding_vector_blob(vector)

    assert decode_embedding_vector_blob(b"") is None
    assert decode_embedding_vector_blob(blob[:5]) is None  # truncated header
    assert decode_embedding_vector_blob(blob[:-4]) is None  # short payload
    assert decode_embedding_vector_blob(b"XXXX" + blob[4:]) is None  # bad magic
    assert decode_embedding_vector_blob(b"OBLV" + b"\x02\x00" + blob[6:]) is None  # bad version
    assert (
        decode_embedding_vector_blob(b"OBLV" + b"\x01\x00" + b"\x02" + blob[7:]) is None
    )  # bad dtype
    assert (
        decode_embedding_vector_blob(
            b"OBLV" + blob[4:7] + b"\x00" + b"\xff\xff\xff\x7f" + blob[12:]
        )
        is None
    )  # absurd dimension
    assert decode_embedding_vector_blob(blob + b"extra") is None  # trailing junk
    assert decode_embedding_vector_blob("not bytes") is None


def test_payload_decode_handles_json_and_blob_and_mixed() -> None:
    json_payload = "[0.1, 0.2, 0.3]"
    blob_payload = encode_embedding_vector_blob([0.1, 0.2, 0.3])

    assert decode_embedding_vector_payload(json_payload) == pytest.approx([0.1, 0.2, 0.3])
    assert decode_embedding_vector_payload(blob_payload) == pytest.approx([0.1, 0.2, 0.3])
    # A JSON payload stored as bytes (downgraded writer) is still readable.
    assert decode_embedding_vector_payload(json_payload.encode("utf-8")) == pytest.approx(
        [0.1, 0.2, 0.3]
    )
    assert decode_embedding_vector_payload(b"\x00\x01not-a-vector") is None
    assert decode_embedding_vector_payload('[1, "oops", 3]') is None


def test_split_l2_model_namespace_marks_legacy_rows() -> None:
    base, namespace = split_l2_model_namespace("bge-m3#namespace=abc123def")
    assert base == "bge-m3"
    assert namespace == "abc123def"
    assert split_l2_model_namespace("bge-m3") == ("bge-m3", None)
    assert split_l2_model_namespace("bge-m3#dim=1024#namespace=xyz") == (
        "bge-m3#dim=1024",
        "xyz",
    )


def test_put_stores_versioned_blob_with_metadata(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()

    cache.put("key", [0.1, 0.2, 0.3], model="bge-m3")

    row = cache.conn.execute(
        "SELECT vector, encoding, dimension, typeof(vector) FROM embedding_cache"
    ).fetchone()
    assert row is not None
    payload, encoding, dimension, storage_type = row
    assert encoding == 1
    assert dimension == 3
    assert storage_type == "blob"
    assert isinstance(payload, bytes)
    assert decode_embedding_vector_blob(payload) == pytest.approx([0.1, 0.2, 0.3])
    assert cache.get("key", model="bge-m3") == pytest.approx([0.1, 0.2, 0.3])


def test_legacy_json_row_remains_readable(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) VALUES (?, ?, ?, 0)",
        ("legacy-key", "bge-m3", "[0.5, 0.6, 0.7]"),
    )
    cache.conn.commit()

    assert cache.get("legacy-key", model="bge-m3") == pytest.approx([0.5, 0.6, 0.7])


def test_schema_upgrade_from_v1_preserves_rows_as_legacy(tmp_path: Path) -> None:
    # Simulate the pre-v2 table exactly as old binaries created it.
    db_path = tmp_path / "embedding-cache.db"
    raw = sqlite3.connect(db_path)
    raw.execute(
        """CREATE TABLE embedding_cache (
            text_key TEXT NOT NULL,
            model    TEXT NOT NULL DEFAULT '',
            vector   TEXT NOT NULL,
            PRIMARY KEY (text_key, model)
        )"""
    )
    raw.execute(
        "INSERT INTO embedding_cache (text_key, model, vector) VALUES ('k1', 'm1', '[0.1, 0.2]')"
    )
    raw.execute("INSERT INTO embedding_cache (text_key, model, vector) VALUES ('k2', '', '[0.3]')")
    raw.commit()
    raw.close()

    cache = EmbeddingCache(db_path)
    cache.initialize()

    assert cache.count() == 2
    assert cache.get("k1", model="m1") == pytest.approx([0.1, 0.2])
    assert cache.get("k2", model="") == pytest.approx([0.3])
    row = cache.conn.execute(
        "SELECT encoding FROM embedding_cache WHERE text_key = 'k1'"
    ).fetchone()
    assert row[0] == 0  # preserved as legacy JSON, migrated later
    columns = {
        str(r[1]) for r in cache.conn.execute("PRAGMA table_info(embedding_cache)").fetchall()
    }
    assert {"encoding", "dimension", "created_at", "last_accessed_at"} <= columns


def test_migrate_encoding_converts_json_to_blob_and_is_idempotent(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.put("k1", [0.1, 0.2], model="m#namespace=n1")  # blob row, untouched
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) "
        "VALUES ('k2', 'm#namespace=n1', ?, 0)",
        ("[0.3, 0.4, 0.5]",),
    )
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) "
        "VALUES ('k3', 'm#namespace=n1', ?, 0)",
        ("[0.6]",),
    )
    cache.conn.commit()

    report = cache.migrate_encoding(batch_size=1)
    assert report["migrated"] == 2
    assert report["skipped_corrupt"] == 0
    assert report["remaining"] == 0
    assert cache.get("k2", model="m#namespace=n1") == pytest.approx([0.3, 0.4, 0.5])
    assert cache.get("k3", model="m#namespace=n1") == pytest.approx([0.6])

    # Idempotent: a second run converts nothing.
    second = cache.migrate_encoding(batch_size=1)
    assert second["migrated"] == 0
    assert second["remaining"] == 0

    rows = cache.conn.execute(
        "SELECT encoding, dimension, typeof(vector) FROM embedding_cache ORDER BY text_key"
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (1, 2, "blob"),
        (1, 3, "blob"),
        (1, 1, "blob"),
    ]


def test_migrate_encoding_resumes_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openbiliclaw.llm.embedding as emb_module

    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    for i in range(3):
        cache.conn.execute(
            "INSERT INTO embedding_cache (text_key, model, vector, encoding) VALUES (?, 'm', ?, 0)",
            (f"k{i}", f"[{i}.5]"),
        )
    cache.conn.commit()

    # Simulate a crash mid-migration: the second batch raises, but the first
    # batch has already committed (durable progress = the encoding column).
    real_decode = emb_module.decode_embedding_vector_payload
    calls = {"crash_at": 2}

    def flaky(payload: str | bytes) -> list[float] | None:
        if calls["crash_at"] > 0:
            calls["crash_at"] -= 1
            if calls["crash_at"] == 0:
                raise RuntimeError("simulated crash mid-migration")
        return real_decode(payload)

    monkeypatch.setattr(emb_module, "decode_embedding_vector_payload", flaky)
    with pytest.raises(RuntimeError):
        cache.migrate_encoding(batch_size=1)
    assert cache.pending_migration_rows() == 2  # first batch committed

    # A downgraded writer may rewrite a row as JSON afterwards.
    cache.conn.execute(
        "INSERT OR REPLACE INTO embedding_cache (text_key, model, vector, encoding) "
        "VALUES ('k1', 'm', ?, 0)",
        ("[9.9]",),
    )
    cache.conn.commit()

    # Re-running continues from where the crash left off, no data lost.
    second = cache.migrate_encoding(batch_size=1)
    assert second["migrated"] == 2  # k1 (rewritten) + k2
    assert cache.pending_migration_rows() == 0
    assert cache.get("k0", model="m") == pytest.approx([0.5])
    assert cache.get("k1", model="m") == pytest.approx([9.9])
    assert cache.get("k2", model="m") == pytest.approx([2.5])


def test_migrate_encoding_skips_corrupt_row_without_blocking(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) "
        "VALUES ('good', 'm', ?, 0)",
        ("[0.1, 0.2]",),
    )
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) VALUES ('bad', 'm', ?, 0)",
        ("[0.1, oops]",),  # corrupt JSON
    )
    cache.conn.commit()

    report = cache.migrate_encoding()
    assert report["migrated"] == 1
    assert report["skipped_corrupt"] == 1
    assert report["remaining"] == 0
    # The corrupt row is marked (never retried) without blocking the good one.
    row = cache.conn.execute(
        "SELECT encoding FROM embedding_cache WHERE text_key = 'bad'"
    ).fetchone()
    assert row[0] == -1
    assert cache.get("good", model="m") == pytest.approx([0.1, 0.2])
    assert cache.get("bad", model="m") is None
    # Re-running does not churn on the corrupt row again.
    second = cache.migrate_encoding()
    assert second["skipped_corrupt"] == 0


# ---------------------------------------------------------------------------
# Namespace lifecycle + capacity policy (issue #153)
# ---------------------------------------------------------------------------


def test_stats_classifies_legacy_active_and_inactive_namespaces(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.put("a", [0.1], model="m#namespace=active-ns")
    cache.put("b", [0.2], model="m#namespace=active-ns")
    cache.put("c", [0.3], model="m#namespace=dead-ns")
    cache.put("d", [0.4], model="plain-legacy")

    stats = cache.stats(active_models={"m#namespace=active-ns"})

    assert stats["total_rows"] == 4
    assert stats["legacy_rows"] == 1
    assert stats["namespaced_rows"] == 3
    assert stats["active_rows"] == 2
    assert stats["inactive_rows"] == 2
    assert stats["capacity"]["max_bytes"] == 0  # unlimited by default
    by_model = {e["model"]: e for e in stats["namespaces"]}
    assert by_model["m#namespace=active-ns"]["active"] is True
    assert by_model["m#namespace=dead-ns"]["active"] is False
    assert by_model["plain-legacy"]["namespace"] is None
    assert by_model["plain-legacy"]["active"] is False


def test_maintain_evicts_inactive_before_active_and_stops_at_low_watermark(
    tmp_path: Path,
) -> None:
    cache = EmbeddingCache(
        tmp_path / "embedding-cache.db",
        max_bytes=1024,
        high_watermark=0.9,
        low_watermark=0.5,
    )
    cache.initialize()
    blob = encode_embedding_vector_blob([0.5] * 100)  # 412 bytes per row
    for i in range(6):
        cache.put(f"active-{i}", [0.5] * 100, model="m#namespace=active-ns")
    # Dead-namespace rows are inserted directly so they are never registered
    # as active by the write path.
    for i in range(6):
        cache.conn.execute(
            "INSERT INTO embedding_cache (text_key, model, vector, encoding, dimension) "
            "VALUES (?, ?, ?, 1, 100)",
            (f"dead-{i}", "m#namespace=dead-ns", blob),
        )
    cache.conn.commit()

    report = cache.maintain()

    assert report["deleted_rows"] > 0
    dead_remaining = cache.conn.execute(
        "SELECT COUNT(*) FROM embedding_cache WHERE model = 'm#namespace=dead-ns'"
    ).fetchone()[0]
    assert dead_remaining == 0  # inactive namespace reclaimed first
    assert report["after_bytes"] <= 1024 * 0.5  # converged to low watermark
    # Active rows were only partially evicted: one survives.
    active_remaining = cache.conn.execute(
        "SELECT COUNT(*) FROM embedding_cache WHERE model = 'm#namespace=active-ns'"
    ).fetchone()[0]
    assert active_remaining == 1


def test_maintain_noop_when_unlimited_or_under_high_watermark(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db", max_bytes=0)
    cache.initialize()
    cache.put("a", [0.1], model="m")
    assert cache.maintain()["skipped_reason"] == "unlimited"

    capped = EmbeddingCache(
        tmp_path / "capped.db", max_bytes=1024 * 1024, high_watermark=0.9, low_watermark=0.5
    )
    capped.initialize()
    capped.put("a", [0.1], model="m")
    assert capped.maintain()["skipped_reason"] == "under_high_watermark"
    assert capped.maintain()["deleted_rows"] == 0


def test_put_cadence_enforces_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("openbiliclaw.llm.embedding._MAINTENANCE_WRITE_INTERVAL", 3)
    cache = EmbeddingCache(
        tmp_path / "embedding-cache.db",
        max_bytes=1200,
        high_watermark=0.9,
        low_watermark=0.5,
    )
    cache.initialize()
    for i in range(30):
        cache.put(f"k{i}", [0.5] * 100, model="m#namespace=ns1")  # 412 bytes each

    # Sustained writes converge to the configured budget instead of growing
    # without bound.
    assert cache._stored_bytes() <= 1200 * 0.5
    assert cache.count() < 30


def test_delete_inactive_dry_run_then_apply(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.put("a", [0.1], model="m#namespace=active-ns")
    cache.put("b", [0.2], model="m#namespace=dead-ns")
    cache.put("c", [0.3], model="plain-legacy")

    dry = cache.delete_inactive({"m#namespace=active-ns"}, dry_run=True)
    assert dry["deleted_rows"] == 2
    assert dry["dry_run"] is True
    assert cache.count() == 3  # untouched

    deleted = cache.delete_inactive({"m#namespace=active-ns"})
    assert deleted["deleted_rows"] == 2
    assert cache.count() == 1
    assert cache.get("a", model="m#namespace=active-ns") is not None


def test_delete_inactive_keep_legacy_preserves_non_namespaced_rows(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.put("a", [0.1], model="m#namespace=active-ns")
    cache.put("b", [0.2], model="m#namespace=dead-ns")
    cache.put("c", [0.3], model="plain-legacy")

    deleted = cache.delete_inactive({"m#namespace=active-ns"}, keep_legacy=True)
    assert deleted["deleted_rows"] == 1  # only the dead namespace
    assert cache.count() == 2
    assert cache.get("c", model="plain-legacy") is not None


def test_compact_shrinks_file_and_preserves_data(tmp_path: Path) -> None:
    def total_size(cache: EmbeddingCache) -> int:
        # In WAL mode the live data may live in the -wal file, so the main
        # file size alone is not a meaningful disk footprint.
        total = cache._db_path.stat().st_size
        for suffix in ("-wal", "-shm"):
            side = Path(str(cache._db_path) + suffix)
            if side.exists():
                total += side.stat().st_size
        return total

    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    for i in range(200):
        cache.put(f"k{i}", [0.1] * 64, model="m#namespace=ns1")
    before = total_size(cache)

    # DELETE alone moves pages to the freelist; disk footprint does not drop
    # (in WAL mode it even grows until the next checkpoint).
    cache.conn.execute("DELETE FROM embedding_cache WHERE rowid % 2 = 1")
    cache.conn.commit()
    assert total_size(cache) >= before

    report = cache.compact()
    assert report["ok"] is True
    assert cache.count() == 100
    assert cache.get("k1", model="m#namespace=ns1") == pytest.approx([0.1] * 64)
    assert cache.get("k0", model="m#namespace=ns1") is None
    # Rebuild reclaimed the freelist + truncated WAL: total footprint shrank.
    assert total_size(cache) < before

    # The compacted file opens cleanly in a fresh connection.
    cache.close()
    reopened = EmbeddingCache(tmp_path / "embedding-cache.db")
    reopened.initialize()
    assert reopened.count() == 100
    assert reopened.get("k3", model="m#namespace=ns1") == pytest.approx([0.1] * 64)


def test_compact_failure_leaves_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.put("k", [0.1, 0.2], model="m")

    def _boom(src: str, dst: str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("openbiliclaw.llm.embedding.os.replace", _boom)

    report = cache.compact()
    assert report["ok"] is False
    assert report["error"]

    # The original file was never replaced and the connection is usable again.
    assert cache.get("k", model="m") == pytest.approx([0.1, 0.2])
    assert not Path(str(cache._db_path) + ".compact.tmp").exists()  # temp cleaned up


def test_get_bumps_last_accessed_at(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.put("k", [0.1], model="m")
    cache.conn.execute("UPDATE embedding_cache SET last_accessed_at = 0 WHERE text_key = 'k'")
    cache.conn.commit()

    cache.get("k", model="m")

    bumped = cache.conn.execute(
        "SELECT last_accessed_at FROM embedding_cache WHERE text_key = 'k'"
    ).fetchone()[0]
    assert int(bumped) > 0


def test_put_many_writes_in_single_transaction(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.put_many([("a", [0.1], "m1"), ("b", [0.2, 0.3], "m2"), ("c", [0.4], "m1")])
    assert cache.count() == 3
    assert cache.get("a", model="m1") == pytest.approx([0.1])
    assert cache.get("b", model="m2") == pytest.approx([0.2, 0.3])
    cache.put_many([])  # no-op
    assert cache.count() == 3


def test_prepare_for_runtime_migrates_once_per_process(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) "
        "VALUES ('legacy', 'm', ?, 0)",
        ("[0.5, 0.6]",),
    )
    cache.conn.commit()
    cache.register_active_model("m")

    first = cache.prepare_for_runtime(max_bytes=1024, high_watermark=0.9, low_watermark=0.5)
    assert first["migration"]["migrated"] == 1
    assert cache.pending_migration_rows() == 0

    # The one-time-per-process guard skips the migration on later prepares.
    second = cache.prepare_for_runtime(max_bytes=1024, high_watermark=0.9, low_watermark=0.5)
    assert "migration" not in second


async def test_embedding_service_enforces_byte_budget_via_write_cadence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("openbiliclaw.llm.embedding._MAINTENANCE_WRITE_INTERVAL", 3)
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    provider = _FakeEmbedProvider(vector=[0.1] * 100)
    service = EmbeddingService(
        provider,
        model="bge-m3",
        persistent_cache=cache,
        cache_max_bytes=1200,
        cache_high_watermark=0.9,
        cache_low_watermark=0.5,
    )
    for i in range(30):
        await service.embed(f"text {i}")
    assert cache._stored_bytes() <= 1200 * 0.5


async def test_embedding_service_exposes_l2_cache_and_stats(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "embedding-cache.db")
    cache.initialize()
    provider = _FakeEmbedProvider(vector=[0.1, 0.2, 0.3])
    service = EmbeddingService(provider, model="bge-m3", persistent_cache=cache)

    assert service.l2_cache is cache
    assert service.l2_cache_stats()["total_rows"] == 0

    await service.embed("hello")

    stats = service.l2_cache_stats()
    assert stats["total_rows"] == 1
    assert stats["active_rows"] == 1
    assert service.l2_cache.active_models() == {service.cache_model_namespace}


async def test_embedding_breaker_skips_dead_provider_after_repeated_failures() -> None:
    provider = _FakeEmbedProvider(error=RuntimeError("ConnectError: localhost:11434"))
    service = EmbeddingService(
        provider,
        model="bge-m3",
        breaker_failure_threshold=3,
        breaker_cooldown_seconds=60,
    )

    # The first two failures still touch the provider; the third opens the breaker.
    assert await service.embed("one") == []
    assert await service.embed("two") == []
    assert await service.embed("three") == []
    assert len(provider.calls) == 3

    # During cooldown no provider calls happen; each call returns [] cheaply.
    assert await service.embed("four") == []
    assert await service.embed("five") == []
    assert len(provider.calls) == 3


async def test_embedding_breaker_reprobes_after_cooldown() -> None:
    provider = _FakeEmbedProvider(error=RuntimeError("ConnectError"))
    service = EmbeddingService(
        provider,
        model="bge-m3",
        breaker_failure_threshold=2,
        breaker_cooldown_seconds=0.01,
    )

    assert await service.embed("one") == []
    assert await service.embed("two") == []
    assert len(provider.calls) == 2

    # After the cooldown elapses the next call re-probes the provider.
    import asyncio

    await asyncio.sleep(0.02)
    assert await service.embed("three") == []
    assert len(provider.calls) == 3


async def test_embedding_breaker_counts_empty_vectors_as_failures() -> None:
    provider = _FakeEmbedProvider(vector=[])
    service = EmbeddingService(
        provider,
        model="bge-m3",
        breaker_failure_threshold=2,
        breaker_cooldown_seconds=60,
    )

    assert await service.embed("one") == []
    assert await service.embed("two") == []
    assert await service.embed("three") == []

    assert len(provider.calls) == 2  # opened after the second empty response


async def test_embedding_breaker_resets_streak_after_text_embed_success() -> None:
    provider = _ScriptedEmbedProvider(
        [
            RuntimeError("ConnectError"),
            [0.1, 0.2, 0.3],
            RuntimeError("ConnectError"),
            [0.4, 0.5, 0.6],
        ]
    )
    service = EmbeddingService(
        provider,
        model="bge-m3",
        breaker_failure_threshold=2,
        breaker_cooldown_seconds=60,
    )

    assert await service.embed("one") == []
    assert await service.embed("two") == [0.1, 0.2, 0.3]
    assert await service.embed("three") == []
    # The second failure is not consecutive: the success in between reset the
    # streak, so this call must hit the provider instead of skipping it.
    assert await service.embed("four") == [0.4, 0.5, 0.6]
    assert len(provider.calls) == 4


async def test_embedding_breaker_resets_streak_after_document_embed_success() -> None:
    provider = _ScriptedEmbedProvider(
        [
            RuntimeError("ConnectError"),
            [0.1, 0.2, 0.3],
            RuntimeError("ConnectError"),
            [0.4, 0.5, 0.6],
        ]
    )
    service = EmbeddingService(
        provider,
        model="bge-m3",
        breaker_failure_threshold=2,
        breaker_cooldown_seconds=60,
    )

    assert await service.embed_document("one") == []
    assert await service.embed_document("two") == [0.1, 0.2, 0.3]
    assert await service.embed_document("three") == []
    assert await service.embed_document("four") == [0.4, 0.5, 0.6]
    assert len(provider.calls) == 4


async def test_embedding_breaker_resets_streak_after_probe_success() -> None:
    provider = _ScriptedEmbedProvider(
        [
            RuntimeError("ConnectError"),
            [0.1, 0.2, 0.3],
            RuntimeError("ConnectError"),
            [0.4, 0.5, 0.6],
        ]
    )
    service = EmbeddingService(
        provider,
        model="bge-m3",
        breaker_failure_threshold=2,
        breaker_cooldown_seconds=60,
    )

    assert await service.embed("one") == []
    assert await service.probe() is True
    assert await service.embed("two") == []
    assert await service.embed("three") == [0.4, 0.5, 0.6]
    assert len(provider.calls) == 4


async def test_embedding_breaker_resets_streak_after_image_embed_success() -> None:
    class _ScriptedImageEmbedProvider:
        supports_image_embedding = True

        def __init__(self) -> None:
            self.image_calls: list[int] = []
            self._script: list[list[float] | Exception] = [
                RuntimeError("ConnectError"),
                [0.9, 0.1, 0.0],
                RuntimeError("ConnectError"),
                [0.8, 0.2, 0.1],
            ]

        @staticmethod
        def is_multimodal_embedding_model(model: str) -> bool:
            return True

        async def embed_image(
            self,
            image_bytes: bytes,
            *,
            mime_type: str = "image/jpeg",
            model: str = "",
        ) -> list[float]:
            self.image_calls.append(len(image_bytes))
            result = self._script.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    provider = _ScriptedImageEmbedProvider()
    service = EmbeddingService(
        provider,
        model="gemini-embedding-2",
        multimodal_enabled=True,
        breaker_failure_threshold=2,
        breaker_cooldown_seconds=60,
    )
    # Use a different image for each call so the L1/L2 cache cannot mask a
    # provider call after the first success.
    images = [b"cover-one", b"cover-two", b"cover-three", b"cover-four"]

    assert await service.embed_image(images[0]) == []
    assert await service.embed_image(images[1]) == [0.9, 0.1, 0.0]
    assert await service.embed_image(images[2]) == []
    assert await service.embed_image(images[3]) == [0.8, 0.2, 0.1]
    assert len(provider.image_calls) == 4


def test_float32_blob_cosine_error_is_within_convention() -> None:
    import random

    rng = random.Random(42)
    vector = [rng.uniform(-1.0, 1.0) for _ in range(4096)]
    decoded = decode_embedding_vector_blob(encode_embedding_vector_blob(vector))
    assert decoded is not None
    # float32 quantization must not degrade similarity decisions (thresholds
    # live around 0.82; error here is ~1e-7).
    assert cosine_similarity(vector, decoded) >= 0.9999
