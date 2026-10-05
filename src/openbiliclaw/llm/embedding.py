"""Embedding service with two-layer caching for semantic similarity.

Provides text embedding via configurable models (default: Gemini),
with L1 in-memory cache and L2 SQLite persistent cache.
Discovery writes embeddings to L2; recommendation reads from L2
with zero API calls on the hot path.

Optional image embedding (cover-only vectors) is gated by
``[llm.embedding].multimodal_enabled`` plus a provider/model that
supports native image embed (e.g. Gemini Embedding 2). Image vectors
share the same cache table under ``img:`` keys and the same model
namespace so they stay in one vector space with text embeds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import sqlite3
import struct
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from openbiliclaw.diagnostics_alerts import record_diagnostics_alert

logger = logging.getLogger(__name__)

_IMAGE_CACHE_KEY_PREFIX = "img:"

# ---------------------------------------------------------------------------
# Versioned binary vector encoding (L2 storage format)
#
# Rows are stored as little-endian float32 payloads behind a fixed header so a
# 4096-dim vector takes ~16 KiB instead of ~90 KiB of JSON text. The header is
# self-describing: a reader must never infer the format from the SQLite column
# type alone (a BLOB-affinity column can legitimately hold legacy JSON text
# written by older binaries).
#
# Layout (all little-endian):
#   magic     4 bytes  b"OBLV"
#   version   uint16   = 1 (increment on any format change)
#   dtype     uint8    = 1 (float32); 2 reserved for float64
#   reserved  uint8    = 0
#   dimension uint32   vector length
#   payload   dimension * 4 bytes of float32
# ---------------------------------------------------------------------------
_EMBEDDING_BLOB_MAGIC = b"OBLV"
_EMBEDDING_BLOB_VERSION = 1
_EMBEDDING_BLOB_DTYPE_FLOAT32 = 1
_EMBEDDING_BLOB_DTYPE_FLOAT64 = 2  # reserved; not produced today
_EMBEDDING_BLOB_HEADER = struct.Struct("<4sHBBI")

# ``encoding`` column values on embedding_cache rows.
_ENCODING_LEGACY_JSON = 0  # JSON text written by <= v0.3.x binaries
_ENCODING_OBLV_FLOAT32 = 1  # versioned little-endian float32 blob (current)
_ENCODING_CORRUPT = -1  # undecodable payload; skipped by migration

# How often a cached key's ``last_accessed_at`` may be refreshed. Bumping the
# timestamp on every hit would turn a read path into a write path; once per
# minute per key keeps eviction ordering fresh with bounded write traffic.
_ACCESS_BUMP_INTERVAL_SECONDS = 60.0

# put() checks the configured byte budget at most once every N writes so a
# burst of warmup embeds never pays an eviction pass per row.
_MAINTENANCE_WRITE_INTERVAL = 128

# Namespace marker used by EmbeddingService to qualify the L2 ``model`` key:
# ``<cache_model>#namespace=<sha256-of-provenance>``. Rows without this marker
# are "legacy" rows written before provenance isolation existed.
_NAMESPACE_MARKER = "#namespace="

# Runtime preparation (encoding migration + first maintenance) runs once per
# process per database file, even though several EmbeddingService instances
# (daemon bootstrap, config probes, hot reload) may open the same cache.
_PREPARED_DB_PATHS: set[str] = set()
_PREPARED_DB_PATHS_LOCK = threading.Lock()


def encode_embedding_vector_blob(vector: list[float]) -> bytes:
    """Encode a vector as the versioned OBLV float32 payload."""
    dimension = len(vector)
    header = _EMBEDDING_BLOB_HEADER.pack(
        _EMBEDDING_BLOB_MAGIC,
        _EMBEDDING_BLOB_VERSION,
        _EMBEDDING_BLOB_DTYPE_FLOAT32,
        0,
        dimension,
    )
    return header + struct.pack(f"<{dimension}f", *vector)


def decode_embedding_vector_blob(payload: bytes) -> list[float] | None:
    """Decode an OBLV float32 payload, or ``None`` for any malformed input.

    Validation is defensive and per-row: a single corrupt or unknown-format
    row must degrade to a cache miss, never crash or poison the whole cache.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        return None
    if len(payload) < _EMBEDDING_BLOB_HEADER.size:
        return None
    magic, version, dtype, _reserved, dimension = _EMBEDDING_BLOB_HEADER.unpack_from(payload)
    if magic != _EMBEDDING_BLOB_MAGIC:
        return None
    if version != _EMBEDDING_BLOB_VERSION or dtype != _EMBEDDING_BLOB_DTYPE_FLOAT32:
        return None
    if dimension < 1 or dimension > 10_000_000:
        return None
    expected = _EMBEDDING_BLOB_HEADER.size + dimension * 4
    if len(payload) != expected:
        return None
    floats = struct.unpack(f"<{dimension}f", payload[_EMBEDDING_BLOB_HEADER.size :])
    return [float(value) for value in floats]


def decode_embedding_vector_payload(payload: str | bytes) -> list[float] | None:
    """Decode a cached payload that may be JSON text or an OBLV blob.

    Content is detected, never trusted from the column type: downgraded
    binaries can rewrite the same primary key as JSON, so a BLOB column can
    legitimately hold either format at any time (mixed-format tolerance).
    """
    if isinstance(payload, bytes):
        blob_vector = decode_embedding_vector_blob(payload)
        if blob_vector is not None:
            return blob_vector
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        return _coerce_embedding_vector(json.loads(payload))
    except (json.JSONDecodeError, TypeError):
        return None


def split_l2_model_namespace(model: str) -> tuple[str, str | None]:
    """Split an L2 ``model`` key into ``(base_model, namespace|None)``.

    Namespaced keys look like ``<model>#namespace=<fingerprint>``. Rows
    without the marker are legacy (pre-provenance) rows.
    """
    base, separator, namespace = (model or "").partition(_NAMESPACE_MARKER)
    if not separator:
        return model or "", None
    return base, namespace


def normalize_embedding_endpoint(endpoint: str) -> str:
    """Return a stable, non-secret endpoint identity for embedding provenance.

    Endpoint query strings and fragments commonly contain API keys, signed
    parameters, or UI-only state, so they are deliberately discarded. User
    info is also removed before the host is rendered. The result retains only
    the transport, host/port, and path that identify the actual service.
    """
    raw = str(endpoint or "").strip()
    if not raw:
        return ""

    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        # Keep malformed/custom endpoint strings useful without ever carrying
        # query or fragment material into the namespace.
        redacted = raw.split("?", 1)[0].split("#", 1)[0]
        # Scheme-less endpoint strings are accepted by a few compatible
        # clients. They can still contain ``userinfo@host``; discard that
        # prefix before retaining the deterministic custom identity.
        return redacted.rsplit("@", 1)[-1].rstrip("/")

    try:
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        # An invalid port is not a usable URL, but the redacted path still
        # gives callers a deterministic namespace rather than leaking the raw
        # user-info/query-bearing string.
        hostname = (parsed.netloc.rsplit("@", 1)[-1]).split(":", 1)[0]
        port = None

    hostname = hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    hostport = hostname
    if port is not None and not default_port:
        hostport = f"{hostport}:{port}"

    path = parsed.path.rstrip("/")
    redacted_url = SplitResult(parsed.scheme.lower(), hostport, path, "", "")
    return urlunsplit(redacted_url)


def build_embedding_provenance(
    logical_provider: str,
    endpoint: str,
    model: str,
    output_dimensionality: int = 0,
) -> str:
    """Build deterministic provenance without credentials or request data."""
    payload = {
        "provider": str(logical_provider or "").strip().lower(),
        "endpoint": normalize_embedding_endpoint(endpoint),
        "model": str(model or "").strip(),
        "dimension": max(0, int(output_dimensionality or 0)),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _provider_endpoint(provider: SupportsEmbed) -> str:
    """Best-effort endpoint discovery for direct ``EmbeddingService`` users."""
    for name in ("base_url", "_base_url"):
        value = getattr(provider, name, "")
        if value:
            return str(value)
    return ""


def _provider_logical_name(provider: SupportsEmbed) -> str:
    value = getattr(provider, "name", "")
    if value:
        return str(value).strip().lower()
    provider_type = type(provider)
    return f"{provider_type.__module__}.{provider_type.__qualname__}"


def _provider_output_dimensionality(provider: SupportsEmbed) -> int:
    for name in ("embedding_output_dimensionality", "_embedding_output_dimensionality"):
        value = getattr(provider, name, 0)
        try:
            dimension = int(value or 0)
        except (TypeError, ValueError):
            continue
        if dimension > 0:
            return dimension
    return 0


class SupportsEmbed(Protocol):
    """Protocol for providers that support text embedding."""

    async def embed(self, text: str, *, model: str = ...) -> list[float]: ...


class SupportsEmbeddingService(Protocol):
    """Protocol for semantic embedding helpers used by mainline services."""

    similarity_threshold: float
    supports_image_embedding: bool
    multimodal_enabled: bool

    async def embed(self, text: str) -> list[float]: ...

    async def embed_document(self, text: str) -> list[float]:
        """Embed full text without the bounded semantic-key contract."""
        return []

    def lookup_cached(self, text: str) -> list[float]:
        """Cache-only lookup; default returns ``[]`` for protocol compatibility."""
        return []

    def lookup_cached_document(self, text: str) -> list[float]:
        """Cache-only lookup for an untruncated document embedding."""
        return []

    def image_embedding_active(self) -> bool:
        """True when config + provider allow image embeds."""
        return False

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        cache_key: str | None = None,
    ) -> list[float]:
        """Image-only embed; default returns ``[]`` when unsupported."""
        return []

    def lookup_cached_image(self, cache_key: str) -> list[float]:
        """Cache-only image lookup; default returns ``[]``."""
        return []


def image_embedding_cache_key(image_bytes: bytes) -> str:
    """Stable L1/L2 key for compressed cover bytes."""
    digest = hashlib.sha256(image_bytes).hexdigest()[:40]
    return f"{_IMAGE_CACHE_KEY_PREFIX}{digest}"


def image_embedding_cache_key_for_url(cover_url: str) -> str:
    """Stable L1/L2 key derived from a cover URL (not its bytes).

    Lets the discovery warmer and the delight consumer agree on one key
    without both re-downloading + re-compressing the cover just to hash the
    bytes: the warmer stores under this key on pool admission, and the hot
    delight path looks it up by URL alone. The URL is normalised (trimmed)
    so the same cover resolves to the same key across call sites.
    """
    normalized = (cover_url or "").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:40]
    return f"{_IMAGE_CACHE_KEY_PREFIX}{digest}"


def keyframe_embedding_cache_key(
    bvid: str,
    frame_index: int,
    sampling_signature: str = "global-even-midpoint-v1|max_frames=4|edge_skip=0.1",
    embedding_fingerprint: str = "",
) -> str:
    """Stable L1/L2 key for one sampled video keyframe.

    Keyed by ``(embedding_fingerprint, sampling_signature, bvid, frame_index)``
    rather than frame bytes so the prewarm writer and ranking reader agree
    without re-downloading and re-cropping
    the sprite sheet just to hash the pixels — the same reason
    :func:`image_embedding_cache_key_for_url` is URL-keyed.

    Shares the ``img:`` namespace (keyframes ARE images, in the same vector
    space as covers) but the ``kf|`` payload prefix keeps them from ever
    colliding with a cover key.
    """
    normalized = (bvid or "").strip()
    signature = (sampling_signature or "").strip() or "legacy"
    fingerprint = (embedding_fingerprint or "").strip() or "legacy"
    payload = f"kf|{fingerprint}|{signature}|{normalized}|{max(0, int(frame_index))}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]
    return f"{_IMAGE_CACHE_KEY_PREFIX}{digest}"


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

    Storage is a versioned little-endian float32 blob (``encoding=1``) but
    legacy JSON rows (``encoding=0``) and even mixed-format rows written by
    downgraded binaries are read transparently. Rows carry ``dimension``,
    ``created_at`` and ``last_accessed_at`` metadata for safe migration and
    time-based eviction.

    Capacity policy (optional): when ``max_bytes > 0`` the cache enforces a
    byte budget with high/low watermarks. Eviction order is:
    1. rows whose model/namespace is not active (unreachable namespaces and
       legacy pre-provenance rows),
    2. oldest / least-recently-used rows within the active namespaces.
    Maintenance runs lazily inside ``put()`` (bounded cadence) so the hot
    write path never pays a per-row eviction pass.

    Thread-safe: the cache is read/written from background discovery and
    recommendation-prewarm workers running on different threads, so the single
    connection is opened with ``check_same_thread=False`` and every access is
    serialized by an ``RLock`` (a bare ``sqlite3`` connection otherwise raises
    "SQLite objects created in a thread can only be used in that same thread").
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_bytes: int = 0,
        high_watermark: float = 0.9,
        low_watermark: float = 0.7,
    ) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._max_bytes = max(0, int(max_bytes or 0))
        high = min(max(float(high_watermark or 0), 0.0), 1.0)
        low = min(max(float(low_watermark or 0), 0.0), 1.0)
        self._high_watermark = max(high, low)
        self._low_watermark = min(high, low)
        if self._low_watermark <= 0:
            self._low_watermark = self._high_watermark * 0.5
        self._active_models: set[str] = set()
        self._write_count = 0
        self._access_bump: dict[tuple[str, str], float] = {}
        self._runtime_prepared = False

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._conn = sqlite3.connect(str(self._db_path), timeout=10.0, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema()
            self._ensure_meta_table()
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
        if column_names >= {
            "text_key",
            "model",
            "vector",
            "encoding",
            "dimension",
            "created_at",
            "last_accessed_at",
        } and pk_columns == ["text_key", "model"]:
            # Current schema already in place.
            return

        # Upgrade path: rename the old table, create the v2 schema and copy
        # every row over as legacy-JSON encoding. The copy is one transaction
        # and idempotent (the old table is dropped only afterwards).
        self.conn.execute("ALTER TABLE embedding_cache RENAME TO embedding_cache_legacy")
        self._create_cache_table()
        legacy_columns = {str(row[1]) for row in columns}
        if {"text_key", "vector"} <= legacy_columns:
            model_expr = "COALESCE(model, '')" if "model" in legacy_columns else "''"
            self.conn.execute(
                f"""INSERT INTO embedding_cache (text_key, model, vector, encoding, dimension)
                    SELECT text_key, {model_expr}, vector, {_ENCODING_LEGACY_JSON}, 0
                    FROM embedding_cache_legacy"""
            )
        self.conn.execute("DROP TABLE embedding_cache_legacy")
        self.conn.commit()

    def _create_cache_table(self) -> None:
        self.conn.execute(
            """CREATE TABLE embedding_cache (
                text_key          TEXT NOT NULL,
                model             TEXT NOT NULL DEFAULT '',
                vector            BLOB NOT NULL,
                encoding          INTEGER NOT NULL DEFAULT 0,
                dimension         INTEGER NOT NULL DEFAULT 0,
                created_at        INTEGER NOT NULL DEFAULT (unixepoch()),
                last_accessed_at  INTEGER NOT NULL DEFAULT (unixepoch()),
                PRIMARY KEY (text_key, model)
            )"""
        )

    def _ensure_meta_table(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS embedding_cache_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_cache_meta (key, value) "
            "VALUES ('schema_version', '2')"
        )

    def _meta_get(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM embedding_cache_meta WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_cache_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def get(self, key: str, model: str = "") -> list[float] | None:
        with self._lock:
            if model:
                row = self.conn.execute(
                    "SELECT vector FROM embedding_cache WHERE text_key = ? AND model = ?",
                    (key, model),
                ).fetchone()
                resolved_model = model
            else:
                row = self.conn.execute(
                    "SELECT vector, model FROM embedding_cache "
                    "WHERE text_key = ? ORDER BY model LIMIT 1",
                    (key,),
                ).fetchone()
                if row is None:
                    return None
                resolved_model = str(row[1] or "")
                row = (row[0],)
        if row is None:
            return None
        vector = decode_embedding_vector_payload(row[0])
        if vector is None:
            return None
        if resolved_model:
            self._active_models.add(resolved_model)
            self._bump_access(key, resolved_model)
        return vector

    def put(self, key: str, vector: list[float], model: str = "") -> None:
        blob = encode_embedding_vector_blob(vector)
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO embedding_cache
                   (text_key, model, vector, encoding, dimension)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, model, blob, _ENCODING_OBLV_FLOAT32, len(vector)),
            )
            self.conn.commit()
            self._active_models.add(model)
            self._write_count += 1
            if self._max_bytes > 0 and self._write_count % _MAINTENANCE_WRITE_INTERVAL == 0:
                self._enforce_capacity()

    def put_many(self, entries: list[tuple[str, list[float], str]]) -> None:
        """Bulk write in one transaction (warmers, migrations, restores).

        ``put()`` keeps a per-write commit so rows written by this process are
        immediately visible to other processes sharing the file; bulk writers
        that accept transaction atomicity can use this instead to avoid the
        per-row sync-commit write amplification.
        """
        if not entries:
            return
        with self._lock:
            self.conn.executemany(
                """INSERT OR REPLACE INTO embedding_cache
                   (text_key, model, vector, encoding, dimension)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        key,
                        model,
                        encode_embedding_vector_blob(vector),
                        _ENCODING_OBLV_FLOAT32,
                        len(vector),
                    )
                    for key, vector, model in entries
                ],
            )
            self.conn.commit()
            self._active_models.update(model for _, _, model in entries)
            self._write_count += len(entries)
            if self._max_bytes > 0:
                self._enforce_capacity()

    def count(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()
        return row[0] if row else 0

    def pending_migration_rows(self) -> int:
        """Rows still stored as legacy JSON (``encoding=0``) awaiting migration."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM embedding_cache WHERE encoding = ?",
                (_ENCODING_LEGACY_JSON,),
            ).fetchone()
        return int(row[0] or 0)

    # ------------------------------------------------------------------
    # Namespace lifecycle + capacity policy
    # ------------------------------------------------------------------

    def register_active_model(self, model: str) -> None:
        """Declare an L2 model key as actively used by the current runtime.

        Eviction treats declared models (plus models written/read this
        process) as the protected set; everything else is reclaimed first
        when the byte budget is exceeded.
        """
        if model:
            self._active_models.add(model)

    def active_models(self) -> set[str]:
        """Copy of the models currently treated as active by this cache."""
        with self._lock:
            return set(self._active_models)

    def set_capacity_policy(
        self,
        *,
        max_bytes: int = 0,
        high_watermark: float = 0.9,
        low_watermark: float = 0.7,
    ) -> None:
        with self._lock:
            self._max_bytes = max(0, int(max_bytes or 0))
            high = min(max(float(high_watermark or 0), 0.0), 1.0)
            low = min(max(float(low_watermark or 0), 0.0), 1.0)
            self._high_watermark = max(high, low)
            self._low_watermark = min(high, low)
            if self._low_watermark <= 0:
                self._low_watermark = self._high_watermark * 0.5

    def prepare_for_runtime(
        self,
        *,
        max_bytes: int = 0,
        high_watermark: float = 0.9,
        low_watermark: float = 0.7,
    ) -> dict[str, object]:
        """One-time runtime preparation: migrate legacy rows + enforce budget.

        Runs once per process per database file. Encoding migration converts
        legacy JSON rows to blobs in small committed batches (resumable and
        idempotent — progress is the ``encoding`` column itself), then the
        capacity policy is applied. Any failure is logged and never disables
        the cache.
        """
        self.set_capacity_policy(
            max_bytes=max_bytes,
            high_watermark=high_watermark,
            low_watermark=low_watermark,
        )
        report: dict[str, object] = {}
        with _PREPARED_DB_PATHS_LOCK:
            first_time = str(self._db_path.resolve()) not in _PREPARED_DB_PATHS
            if first_time:
                _PREPARED_DB_PATHS.add(str(self._db_path.resolve()))
        if first_time:
            try:
                report["migration"] = self.migrate_encoding()
            except Exception:
                logger.warning("Embedding L2 encoding migration failed", exc_info=True)
        if self._max_bytes > 0:
            try:
                report["maintenance"] = self.maintain()
            except Exception:
                logger.warning("Embedding L2 capacity maintenance failed", exc_info=True)
        self._runtime_prepared = True
        return report

    def migrate_encoding(self, batch_size: int = 500) -> dict[str, object]:
        """Convert legacy JSON rows to the OBLV blob format in small batches.

        Idempotent and resumable: only ``encoding=0`` rows are processed, each
        batch commits independently, and an interrupted run simply continues
        where it left off on the next call. Corrupt/unknown payloads are marked
        ``encoding=-1`` (skipped in later runs, cache miss on read) so one bad
        row never blocks the migration.
        """
        migrated = 0
        skipped_corrupt = 0
        batch = max(1, int(batch_size))
        with self._lock:
            while True:
                rows = self.conn.execute(
                    "SELECT rowid, vector FROM embedding_cache "
                    "WHERE encoding = ? ORDER BY rowid LIMIT ?",
                    (_ENCODING_LEGACY_JSON, batch),
                ).fetchall()
                if not rows:
                    break
                for rowid, payload in rows:
                    vector = decode_embedding_vector_payload(payload)
                    if vector is None:
                        self.conn.execute(
                            "UPDATE embedding_cache SET encoding = ? WHERE rowid = ?",
                            (_ENCODING_CORRUPT, rowid),
                        )
                        skipped_corrupt += 1
                        continue
                    self.conn.execute(
                        """UPDATE embedding_cache
                           SET vector = ?, encoding = ?, dimension = ?
                           WHERE rowid = ?""",
                        (
                            encode_embedding_vector_blob(vector),
                            _ENCODING_OBLV_FLOAT32,
                            len(vector),
                            rowid,
                        ),
                    )
                    migrated += 1
                self.conn.commit()
            remaining = self.conn.execute(
                "SELECT COUNT(*) FROM embedding_cache WHERE encoding = ?",
                (_ENCODING_LEGACY_JSON,),
            ).fetchone()[0]
        report: dict[str, object] = {
            "migrated": migrated,
            "skipped_corrupt": skipped_corrupt,
            "remaining": int(remaining or 0),
        }
        self._meta_set("encoding_migration_last", json.dumps(report))
        logger.info("Embedding L2 encoding migration: %s", report)
        return report

    def _stored_bytes(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(LENGTH(vector)), 0) FROM embedding_cache"
        ).fetchone()
        return int(row[0] or 0)

    def _high_target_bytes(self) -> int:
        return int(self._max_bytes * self._high_watermark)

    def _low_target_bytes(self) -> int:
        return int(self._max_bytes * self._low_watermark)

    def _evict_rows(
        self, where_sql: str, params: tuple[object, ...], target_bytes: int, batch: int = 500
    ) -> tuple[int, int]:
        """Delete rows matching ``where_sql`` oldest-first until under target.

        Returns ``(deleted_rows, freed_bytes)``. Deletion is byte-precise
        (only as many rows as needed to cross ``target_bytes``) and done in
        small committed batches so a huge eviction never holds one giant
        transaction or write lock.
        """
        deleted = 0
        freed = 0
        while True:
            current = self._stored_bytes()
            if current <= target_bytes:
                break
            rows = self.conn.execute(
                f"""SELECT rowid, LENGTH(vector) FROM embedding_cache
                    WHERE {where_sql}
                    ORDER BY last_accessed_at ASC, created_at ASC, rowid ASC LIMIT ?""",
                (*params, batch),
            ).fetchall()
            if not rows:
                break
            to_delete: list[tuple[int]] = []
            freed_this_pass = 0
            excess = current - target_bytes
            for rowid, length in rows:
                if freed_this_pass >= excess and to_delete:
                    break
                to_delete.append((rowid,))
                freed_this_pass += int(length or 0)
            if not to_delete:
                break
            self.conn.executemany("DELETE FROM embedding_cache WHERE rowid = ?", to_delete)
            self.conn.commit()
            deleted += len(to_delete)
            freed += freed_this_pass
        return deleted, freed

    def maintain(self) -> dict[str, object]:
        """Enforce the byte budget using the documented eviction order.

        Returns a report of what was deleted. No-op (and cheap) when the
        budget is unlimited or the cache is below the high watermark.
        """
        deleted_total = 0
        freed_total = 0
        stage1_deleted = 0
        stage2_deleted = 0
        skipped_reason = ""
        with self._lock:
            if self._max_bytes <= 0:
                before = self._stored_bytes()
                skipped_reason = "unlimited"
                report: dict[str, object] = {
                    "max_bytes": self._max_bytes,
                    "high_watermark": self._high_watermark,
                    "low_watermark": self._low_watermark,
                    "active_models": sorted(m for m in self._active_models if m),
                    "deleted_rows": 0,
                    "freed_bytes": 0,
                    "before_bytes": before,
                    "after_bytes": before,
                    "skipped_reason": skipped_reason,
                }
                return report
            total = self._stored_bytes()
            if total <= self._high_target_bytes():
                report = {
                    "max_bytes": self._max_bytes,
                    "high_watermark": self._high_watermark,
                    "low_watermark": self._low_watermark,
                    "active_models": sorted(m for m in self._active_models if m),
                    "deleted_rows": 0,
                    "freed_bytes": 0,
                    "before_bytes": total,
                    "after_bytes": total,
                    "skipped_reason": "under_high_watermark",
                }
                return report
            low_target = self._low_target_bytes()
            active = [m for m in self._active_models if m]
            if active:
                placeholders = ",".join("?" for _ in active)
                stage1_deleted, freed = self._evict_rows(
                    f"model NOT IN ({placeholders})", tuple(active), low_target
                )
                deleted_total += stage1_deleted
                freed_total += freed
            if self._stored_bytes() > low_target:
                if active:
                    placeholders = ",".join("?" for _ in active)
                    stage2_deleted, freed = self._evict_rows(
                        f"model IN ({placeholders})", tuple(active), low_target
                    )
                else:
                    stage2_deleted, freed = self._evict_rows("1 = 1", (), low_target)
                deleted_total += stage2_deleted
                freed_total += freed
            report = {
                "max_bytes": self._max_bytes,
                "high_watermark": self._high_watermark,
                "low_watermark": self._low_watermark,
                "active_models": sorted(m for m in self._active_models if m),
                "deleted_rows": deleted_total,
                "freed_bytes": freed_total,
                "stage1_non_active_deleted": stage1_deleted,
                "stage2_active_evicted": stage2_deleted,
                "before_bytes": total,
                "after_bytes": self._stored_bytes(),
            }
        report["completed_at"] = int(time.time())
        self._meta_set("last_maintenance_report", json.dumps(report))
        logger.info("Embedding L2 capacity maintenance: %s", report)
        return report

    def _enforce_capacity(self) -> None:
        """Cheap budget check on the write path; never raises to the caller."""
        try:
            if self._stored_bytes() > self._high_target_bytes():
                self.maintain()
        except Exception:
            logger.warning("Embedding L2 capacity check failed", exc_info=True)

    def delete_inactive(
        self,
        active_models: set[str] | None = None,
        *,
        keep_legacy: bool = False,
        dry_run: bool = False,
    ) -> dict[str, object]:
        """Delete rows outside the active model set (explicit cleanup).

        Legacy (non-namespaced) rows are included unless ``keep_legacy=True``.
        ``dry_run=True`` only reports what would be deleted. Physical space is
        reclaimed only after :meth:`compact`.
        """
        active = set(active_models or self._active_models)
        report: dict[str, object] = {
            "deleted_rows": 0,
            "freed_bytes": 0,
            "dry_run": dry_run,
        }
        with self._lock:
            if active:
                placeholders = ",".join("?" for _ in active)
                where = f"model NOT IN ({placeholders})"
                params: tuple[object, ...] = tuple(active)
            else:
                where = "1 = 1"
                params = ()
            if keep_legacy:
                marker = _NAMESPACE_MARKER.replace("%", "%%").replace("_", "__")
                where = f"({where}) AND model LIKE '%{marker}%'"
            row = self.conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(LENGTH(vector)), 0) "
                f"FROM embedding_cache WHERE {where}",
                params,
            ).fetchone()
            report["deleted_rows"] = int(row[0] or 0)
            report["freed_bytes"] = int(row[1] or 0)
            if report["deleted_rows"] and not dry_run:
                self.conn.execute(f"DELETE FROM embedding_cache WHERE {where}", params)
                self.conn.commit()
        return report

    def stats(self, active_models: set[str] | None = None) -> dict[str, object]:
        """Diagnostics: rows, payload bytes, file/WAL sizes, namespace view."""
        active = set(active_models or self._active_models)
        with self._lock:
            total_rows = self.count()
            stored_bytes = self._stored_bytes()
            file_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
            wal_bytes = 0
            wal_path = Path(str(self._db_path) + "-wal")
            if wal_path.exists():
                wal_bytes = wal_path.stat().st_size
            shm_bytes = 0
            shm_path = Path(str(self._db_path) + "-shm")
            if shm_path.exists():
                shm_bytes = shm_path.stat().st_size

            rows_by_model = self.conn.execute(
                """SELECT model, COUNT(*) AS rows, COALESCE(SUM(LENGTH(vector)), 0) AS bytes
                   FROM embedding_cache GROUP BY model ORDER BY rows DESC"""
            ).fetchall()

            legacy_rows = 0
            legacy_bytes = 0
            namespaced_rows = 0
            namespaced_bytes = 0
            active_rows = 0
            active_bytes = 0
            namespaces: list[dict[str, object]] = []
            for model, rows, bytes_ in rows_by_model:
                model_str = str(model or "")
                base, namespace = split_l2_model_namespace(model_str)
                is_active = model_str in active
                entry: dict[str, object] = {
                    "model": model_str,
                    "base_model": base,
                    "namespace": namespace,
                    "rows": int(rows),
                    "bytes": int(bytes_),
                    "active": is_active,
                }
                namespaces.append(entry)
                if namespace is None:
                    legacy_rows += int(rows)
                    legacy_bytes += int(bytes_)
                else:
                    namespaced_rows += int(rows)
                    namespaced_bytes += int(bytes_)
                if is_active:
                    active_rows += int(rows)
                    active_bytes += int(bytes_)

            last_report = self._meta_get("last_maintenance_report")
            return {
                "db_path": str(self._db_path),
                "total_rows": total_rows,
                "stored_bytes": stored_bytes,
                "file_bytes": file_bytes,
                "wal_bytes": wal_bytes,
                "shm_bytes": shm_bytes,
                "legacy_rows": legacy_rows,
                "legacy_bytes": legacy_bytes,
                "namespaced_rows": namespaced_rows,
                "namespaced_bytes": namespaced_bytes,
                "active_rows": active_rows,
                "active_bytes": active_bytes,
                "inactive_rows": total_rows - active_rows,
                "inactive_bytes": stored_bytes - active_bytes,
                "namespaces": namespaces,
                "capacity": {
                    "max_bytes": self._max_bytes,
                    "high_watermark": self._high_watermark,
                    "low_watermark": self._low_watermark,
                    "over_high_watermark": (
                        self._max_bytes > 0 and stored_bytes > self._high_target_bytes()
                    ),
                },
                "last_maintenance_report": (json.loads(last_report) if last_report else None),
            }

    def compact(self) -> dict[str, object]:
        """Physically shrink the file: checkpoint WAL → VACUUM INTO → verify → swap.

        ``DELETE`` alone only moves pages to the freelist, so the reported
        file size never drops; this rebuilds the database into a new file,
        runs ``PRAGMA integrity_check`` on it and only then atomically
        replaces the original. The original file is left untouched on any
        failure. Other processes must not be actively writing when this runs
        (run it while the daemon is stopped).
        """
        report: dict[str, object] = {"ok": False, "stage": "start", "error": None}
        tmp_path = Path(str(self._db_path) + ".compact.tmp")
        try:
            with self._lock:
                report["stage"] = "wal_checkpoint"
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                report["stage"] = "vacuum_into"
                if tmp_path.exists():
                    tmp_path.unlink()
                quoted = str(tmp_path).replace("'", "''")
                self.conn.execute(f"VACUUM INTO '{quoted}'")
                report["stage"] = "verify"
                verify_conn = sqlite3.connect(str(tmp_path), timeout=10.0)
                try:
                    result = verify_conn.execute("PRAGMA integrity_check").fetchone()
                    if result is None or str(result[0]).strip().lower() != "ok":
                        raise RuntimeError(f"integrity_check on compacted cache failed: {result}")
                    schema_ok = verify_conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'embedding_cache'"
                    ).fetchone()
                    if schema_ok is None:
                        raise RuntimeError("compacted cache is missing embedding_cache table")
                finally:
                    verify_conn.close()
                report["stage"] = "swap"
                before_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
                self.conn.close()
                self._conn = None
                os.replace(str(tmp_path), str(self._db_path))
                for suffix in ("-wal", "-shm"):
                    leftover = Path(str(self._db_path) + suffix)
                    if leftover.exists():
                        leftover.unlink()
                self._conn = sqlite3.connect(
                    str(self._db_path), timeout=10.0, check_same_thread=False
                )
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._ensure_schema()
                self._ensure_meta_table()
                self._conn.commit()
                report["ok"] = True
                report["before_bytes"] = before_bytes
                report["after_bytes"] = self._db_path.stat().st_size
                report["stage"] = "done"
                return report
        except Exception as exc:
            report["ok"] = False
            report["error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("Embedding L2 compact failed: %s", report["error"], exc_info=True)
            try:
                if self._conn is None:
                    self._conn = sqlite3.connect(
                        str(self._db_path), timeout=10.0, check_same_thread=False
                    )
                    self._conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                logger.debug("Failed to reopen embedding cache after compact error")
            return report
        finally:
            if tmp_path.exists():
                with suppress(OSError):
                    tmp_path.unlink()

    def _bump_access(self, key: str, model: str) -> None:
        """Refresh ``last_accessed_at`` at most once per minute per key."""
        now = time.time()
        last = self._access_bump.get((key, model))
        if last is not None and now - last < _ACCESS_BUMP_INTERVAL_SECONDS:
            return
        try:
            self.conn.execute(
                """UPDATE embedding_cache SET last_accessed_at = unixepoch()
                   WHERE text_key = ? AND model = ?""",
                (key, model),
            )
            self.conn.commit()
        except Exception:
            logger.debug("Embedding L2 access-time bump failed", exc_info=True)
            return
        self._access_bump[(key, model)] = now

    def close(self) -> None:
        """Close the persistent cache connection idempotently."""

        with self._lock:
            if self._conn is None:
                return
            self._conn.close()
            self._conn = None


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
        multimodal_enabled: bool = False,
        provenance: str | None = None,
        logical_provider: str = "",
        endpoint: str = "",
        output_dimensionality: int = 0,
        cache_max_bytes: int = 0,
        cache_high_watermark: float = 0.9,
        cache_low_watermark: float = 0.7,
        breaker_failure_threshold: int = 3,
        breaker_cooldown_seconds: float = 60.0,
    ) -> None:
        self._provider = provider
        self._model = model
        self._cache_model = cache_model or model
        self._breaker_failure_threshold = max(1, int(breaker_failure_threshold))
        self._breaker_cooldown_seconds = max(0.0, float(breaker_cooldown_seconds))
        self._breaker_failure_count = 0
        self._breaker_open_until = 0.0
        explicit_provenance = str(provenance or "").strip()
        provider_endpoint = endpoint or _provider_endpoint(provider)
        provider_name = logical_provider or _provider_logical_name(provider)
        requested_dimension = max(0, int(output_dimensionality or 0))
        if not requested_dimension:
            requested_dimension = _provider_output_dimensionality(provider)
        if explicit_provenance:
            self._embedding_provenance = explicit_provenance
        elif logical_provider or endpoint or requested_dimension > 0 or provider_endpoint:
            self._embedding_provenance = build_embedding_provenance(
                provider_name,
                provider_endpoint,
                model,
                requested_dimension,
            )
        else:
            # Preserve the compact legacy cache namespace for small provider
            # doubles and callers that do not expose endpoint provenance. The
            # fingerprint still remains stable and includes provider/model.
            self._embedding_provenance = ""
        self._cache_namespace = (
            hashlib.sha256(self._embedding_provenance.encode("utf-8")).hexdigest()[:32]
            if self._embedding_provenance
            else ""
        )
        # Keep ``_cache_model`` as the caller-visible model identity for
        # compatibility, but use a provenance-qualified model in SQLite. This
        # is the actual L2 namespace and prevents identical text/image keys
        # from crossing endpoint/provider/model boundaries.
        self._l2_cache_model = (
            f"{self._cache_model}#namespace={self._cache_namespace}"
            if self._cache_namespace
            else self._cache_model
        )
        self._embedding_dimension = 0
        # OrderedDict + move_to_end on hit gives us proper LRU instead of
        # FIFO. With a 500-key cache and bursty access patterns (delight
        # scoring iterates the same like_texts repeatedly), FIFO would
        # evict heavy-hit keys whenever the cache filled with cold misses.
        self._l1_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_size = cache_size
        self.similarity_threshold = similarity_threshold
        self._l2_cache = persistent_cache
        if self._l2_cache is not None:
            # Declare this service's namespace as active and run the one-time
            # runtime preparation (legacy JSON → blob migration + first budget
            # pass). Failures degrade to a plain cache, never to a broken one.
            self._l2_cache.register_active_model(self._l2_cache_model)
            try:
                self._l2_cache.prepare_for_runtime(
                    max_bytes=cache_max_bytes,
                    high_watermark=cache_high_watermark,
                    low_watermark=cache_low_watermark,
                )
            except Exception:
                logger.debug("Embedding L2 runtime preparation failed", exc_info=True)
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
        self.multimodal_enabled = bool(multimodal_enabled)
        self.supports_image_embedding = self._detect_image_embedding_support()

    @property
    def embedding_model(self) -> str:
        """Configured provider model used for both text and image vectors."""
        return self._model

    @property
    def embedding_provider(self) -> str:
        """Stable provider implementation identifier for provenance records."""
        provider_type = type(self._provider)
        return f"{provider_type.__module__}.{provider_type.__qualname__}"

    @property
    def embedding_fingerprint(self) -> str:
        """Stable provider/endpoint/model fingerprint, excluding runtime size."""
        provenance = self._embedding_provenance or build_embedding_provenance(
            self.embedding_provider,
            "",
            self._model,
            0,
        )
        payload = f"{provenance}|{self.embedding_provider}|{self._model}|{self._cache_model}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @property
    def cache_model_namespace(self) -> str:
        """The provenance-qualified model key used by the persistent cache."""
        return self._l2_cache_model

    def l2_cache_stats(self) -> dict[str, object]:
        """Diagnostics for the persistent L2 cache (``{}`` when disabled).

        Namespace classification uses this service's own model as the active
        set, matching what the runtime will protect from eviction.
        """
        if self._l2_cache is None:
            return {}
        return self._l2_cache.stats(active_models={self._l2_cache_model})

    @property
    def l2_cache(self) -> EmbeddingCache | None:
        """The persistent L2 cache instance (``None`` when disabled).

        Exposed for diagnostics and maintenance surfaces (CLI cleanup,
        health tooling) that need to operate on the same connection the
        runtime uses, so namespace registration stays consistent.
        """
        return self._l2_cache

    @property
    def embedding_dimension(self) -> int:
        """Observed vector dimension, or zero before the first successful call."""
        return self._embedding_dimension

    def image_embedding_active(self) -> bool:
        """True when config opts in and the provider/model can embed images."""
        return self.multimodal_enabled and self.supports_image_embedding

    def _detect_image_embedding_support(self) -> bool:
        provider = self._provider
        checker = getattr(provider, "is_multimodal_embedding_model", None)
        if callable(checker):
            try:
                if not bool(checker(self._model)):
                    return False
            except Exception:
                return False
        elif not bool(getattr(provider, "supports_image_embedding", False)):
            return False
        return callable(getattr(provider, "embed_image", None))

    def _lookup_cache_key(self, key: str) -> list[float]:
        if not key:
            return []
        cached = self._l1_cache.get(key)
        if cached is not None:
            self._l1_cache.move_to_end(key)
            if cached:
                self._embedding_dimension = len(cached)
            return cached
        if self._l2_cache is not None:
            persisted = self._l2_cache.get(key, model=self._l2_cache_model)
            if persisted is not None:
                self._l1_cache[key] = persisted
                if persisted:
                    self._embedding_dimension = len(persisted)
                return persisted
        return []

    def _store_vector(self, key: str, vector: list[float]) -> None:
        if len(self._l1_cache) >= self._cache_size and key not in self._l1_cache:
            self._l1_cache.popitem(last=False)
        self._l1_cache[key] = vector
        self._embedding_dimension = len(vector)
        if self._l2_cache is not None:
            try:
                self._l2_cache.put(key, vector, model=self._l2_cache_model)
            except Exception:
                logger.debug("L2 cache write failed", exc_info=True)

    def lookup_cached(self, text: str) -> list[float]:
        """Cache-only lookup — never triggers a provider API call.

        Returns ``[]`` on miss. Callers (recommendation hot path) use
        this when they need a hard latency budget: a miss means the
        item simply doesn't participate in embedding-based diversity
        for this batch, and the warmer task fills the cache asynchronously
        for subsequent batches.
        """
        key = text.strip().lower()[:200]
        return self._lookup_cache_key(key)

    def lookup_cached_image(self, cache_key: str) -> list[float]:
        """Cache-only image lookup — never triggers a provider API call."""
        key = (cache_key or "").strip()
        if not key.startswith(_IMAGE_CACHE_KEY_PREFIX):
            return []
        return self._lookup_cache_key(key)

    def _embedding_breaker_open(self) -> bool:
        """Return True while the provider is in a cooldown after repeated failures.

        The breaker is a cheap guard against an unreachable embedding endpoint:
        once the threshold is hit, every call returns ``[]`` without touching
        the provider until the cooldown elapses, then the next call re-probes
        naturally.
        """
        now = time.monotonic()
        if now < self._breaker_open_until:
            return True
        if self._breaker_open_until:
            # Cooldown expired: reset failure state so the next call probes
            # the provider again.
            self._breaker_open_until = 0.0
            self._breaker_failure_count = 0
            logger.info("Embedding breaker closed; provider will be re-probed")
        return False

    def _register_embedding_failure(self) -> None:
        """Count a provider failure and open the breaker at the threshold.

        The failure is deliberately logged once per open (without a full
        traceback) instead of once per call, so an unreachable endpoint cannot
        flood the log with per-call stack traces.
        """
        self._breaker_failure_count += 1
        breaker_tripped = self._breaker_failure_count >= self._breaker_failure_threshold
        if breaker_tripped:
            self._breaker_open_until = time.monotonic() + self._breaker_cooldown_seconds
            logger.warning(
                "Embedding provider failed %d consecutive times; opening circuit "
                "breaker for %.0fs (skipping embedding calls until re-probe).",
                self._breaker_failure_count,
                self._breaker_cooldown_seconds,
            )
        # Surface the anomaly in the diagnostics alert feed (Web 控制台 / 插件
        # 配置页「日志」tab 的异常报警区域)。Breaker trips are error-level;
        # individual failures stay warnings so a flaky endpoint doesn't
        # cry wolf while it still recovers on its own.
        record_diagnostics_alert(
            category="embedding",
            code="breaker_open" if breaker_tripped else "provider_error",
            message=(
                f"Embedding provider 连续失败 {self._breaker_failure_count} 次，"
                f"熔断 {self._breaker_cooldown_seconds:.0f}s。"
                if breaker_tripped
                else "Embedding provider 请求失败（返回空向量或抛出异常）。"
            ),
            source=self._embedding_alert_source(),
            severity="error" if breaker_tripped else "warning",
        )

    def _register_embedding_success(self) -> None:
        """Reset breaker state after a healthy provider response.

        The breaker is documented as tripping on *consecutive* failures. A
        successful non-empty response proves the provider is reachable, so the
        streak restarts at zero rather than accumulating over the lifetime of
        the service, and an already-open breaker is closed early.
        """
        self._breaker_failure_count = 0
        self._breaker_open_until = 0.0

    def _embedding_alert_source(self) -> str:
        """Best-effort label for diagnostics alerts (provider / model)."""
        name = str(getattr(self._provider, "name", "") or "").strip()
        model = str(self._cache_model or self._model or "").strip()
        if name and model:
            return f"{name}/{model}"
        return name or model or "embedding"

    async def embed(self, text: str) -> list[float]:
        """Get embedding for text. Checks L1 → L2 → API."""
        key = text.strip().lower()[:200]
        if not key:
            return []

        # L1 / L2 cache lookup (also covers warming-side hits).
        cached = self.lookup_cached(text)
        if cached:
            return cached
        if self._embedding_breaker_open():
            return []

        # L3: API call (throttled — see __init__ semaphore comment)
        async with self._provider_semaphore:
            try:
                vector = await self._provider.embed(key, model=self._model)
            except Exception:
                self._register_embedding_failure()
                return []

        # Never cache an empty vector. Empty means the provider failed
        # transparently (e.g. swallowed timeout) and returned ``[]``;
        # caching that pins the text to "no embedding" forever even
        # after the upstream issue is fixed. v0.3.31 had ~170 keys
        # poisoned this way before this guard existed — top user
        # interests like 游戏攻略 / 洛克王国 / 金铲铲之战 were affected
        # and the cascade silently zero'd every embedding-derived
        # similarity signal for the most relevant content. Treat empty as a
        # breaker failure so repeated dead-end responses also cool down.
        if not vector:
            self._register_embedding_failure()
            return []

        self._register_embedding_success()
        self._store_vector(key, vector)
        return vector

    def lookup_cached_document(self, text: str) -> list[float]:
        """Look up a full document key without the normal 200-char cap.

        Recommendation/MMR text intentionally uses a bounded key.  Danmaku
        summaries are a separate document contract: silently reducing them to
        the same prefix would make distinct long summaries collide.
        """
        key = text.strip().lower()
        return self._lookup_cache_key(key) if key else []

    async def embed_document(self, text: str) -> list[float]:
        """Embed and cache the complete normalized document text.

        This mirrors :meth:`embed` but deliberately keeps the full summary in
        both the provider request and cache key.  Empty provider results are
        never cached, so a transient failure remains retryable.
        """
        key = text.strip().lower()
        if not key:
            return []
        cached = self.lookup_cached_document(text)
        if cached:
            return cached
        if self._embedding_breaker_open():
            return []
        async with self._provider_semaphore:
            try:
                vector = await self._provider.embed(key, model=self._model)
            except Exception:
                self._register_embedding_failure()
                return []
        if not vector:
            self._register_embedding_failure()
            return []
        self._register_embedding_success()
        self._store_vector(key, vector)
        return vector

    async def embed_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        cache_key: str | None = None,
    ) -> list[float]:
        """Get embedding for image bytes (cover-only). Checks L1 → L2 → API.

        No-ops with ``[]`` when image embedding is inactive (config off or
        provider/model is text-only). Never mixes a different model space
        with text embeds — same ``self._model`` / cache_model namespace.
        """
        if not self.image_embedding_active():
            return []
        if not image_bytes:
            return []

        key = (cache_key or "").strip() or image_embedding_cache_key(image_bytes)
        if not key.startswith(_IMAGE_CACHE_KEY_PREFIX):
            key = image_embedding_cache_key(image_bytes)

        cached = self._lookup_cache_key(key)
        if cached:
            return cached
        if self._embedding_breaker_open():
            return []

        embed_image = getattr(self._provider, "embed_image", None)
        if not callable(embed_image):
            return []

        async with self._provider_semaphore:
            try:
                raw_vector = await embed_image(
                    image_bytes,
                    mime_type=mime_type or "image/jpeg",
                    model=self._model,
                )
            except Exception:
                self._register_embedding_failure()
                return []

        vector = _coerce_embedding_vector(raw_vector) or []
        if not vector:
            self._register_embedding_failure()
            return []

        self._register_embedding_success()
        self._store_vector(key, vector)
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
        if self._embedding_breaker_open():
            return False
        async with self._provider_semaphore:
            try:
                vector = await self._provider.embed(self._PROBE_TEXT, model=self._model)
            except Exception:
                self._register_embedding_failure()
                return False
        if not vector:
            self._register_embedding_failure()
            return False
        self._register_embedding_success()
        return True

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
