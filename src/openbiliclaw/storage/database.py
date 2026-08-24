"""SQLite database management.

Provides async-compatible SQLite operations for event logs,
content cache, and recommendation history.
"""

# [INPUT]: SQLite 数据库、规范化事件与推荐/来源持久化请求
# [OUTPUT]: Database facade、事件写入与查询/迁移能力
# [POS]: 所有 durable 事件字段的最终存储边界，负责兼容迁移而不推断未知来源
# [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import sqlite3
import statistics
import threading
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from uuid import UUID

from openbiliclaw.discovery.admission import (
    DEFAULT_ADMISSION_MIN_SCORE,
    EXPLORE_ADMISSION_MIN_SCORE,
    EXPLORE_STRATEGY,
    effective_admission_threshold,
)
from openbiliclaw.discovery.inspiration import (
    AxisRow,
    _normalize_match_text,
    derive_inspiration_axis_id,
)
from openbiliclaw.discovery.prefilter_audit import (
    PREFILTER_AUDIT_MAX_ROWS,
    PREFILTER_AUDIT_RETENTION_DAYS,
    validate_prefilter_storage_record,
)
from openbiliclaw.discovery.temporal import (
    PUBLICATION_CLOCK_SKEW_TOLERANCE,
    TEMPORAL_CONFIDENCE_FULL,
    TEMPORAL_POLICY_VERSION,
    TemporalEligibilityDecision,
    evaluate_temporal_eligibility,
    ground_temporal_evaluation,
    is_complete_temporal_evidence_marker,
    normalize_temporal_class,
    normalize_temporal_confidence,
    parse_temporal_evaluation,
    schedule_temporal_evaluation,
)
from openbiliclaw.published_time import normalize_published_time
from openbiliclaw.saved_sync.identity import (
    canonical_source_platform,
    content_storage_key,
    make_item_key,
)
from openbiliclaw.saved_sync.models import (
    NATIVE_SAVE_STATUSES,
    NATIVE_SAVE_TERMINAL_STATUSES,
    SavedItemInput,
    SavedListKind,
)
from openbiliclaw.sources.platforms import (
    PLATFORM_BANGUMI as _BANGUMI_SOURCE_FAMILY,
)
from openbiliclaw.sources.platforms import (
    PLATFORM_BILIBILI as _BILIBILI_SOURCE_FAMILY,
)
from openbiliclaw.sources.platforms import (
    PLATFORM_DOUYIN as _DOUYIN_SOURCE_FAMILY,
)
from openbiliclaw.sources.platforms import (
    PLATFORM_REDDIT as _REDDIT_SOURCE_FAMILY,
)
from openbiliclaw.sources.platforms import (
    PLATFORM_XIAOHONGSHU as _XHS_SOURCE_FAMILY,
)
from openbiliclaw.sources.platforms import (
    PLATFORM_YOUTUBE as _YOUTUBE_SOURCE_FAMILY,
)
from openbiliclaw.sources.platforms import (
    SOURCE_CONFIDENCE_EXACT,
    SOURCE_CONFIDENCE_INFERRED,
    SOURCE_CONFIDENCE_LEGACY_UNKNOWN,
    constrain_source_confidence,
    extract_source_content_id,
    infer_source_platform_from_url,
    normalize_source_platform,
    resolve_source_attribution,
)
from openbiliclaw.sources.platforms import (
    source_family as _source_family,
)

if TYPE_CHECKING:
    from openbiliclaw.recommendation.publication_preference import PublicationDatePreference
    from openbiliclaw.saved_sync.extension_broker import ExtensionNativeSaveJob

logger = logging.getLogger(__name__)


def _normalize_content_history_source_platform(value: object) -> str:
    """Use the shared registry while rejecting tokens unsafe for item keys."""
    canonical = normalize_source_platform(str(value or ""))
    if (
        not canonical
        or len(canonical) > 128
        or ":" in canonical
        or any(character.isspace() for character in canonical)
        or any(unicodedata.category(character).startswith("C") for character in canonical)
    ):
        return ""
    return canonical


def _normalize_content_history_item_key(value: object) -> str:
    """Canonicalize only the platform prefix of a persisted item key."""
    raw = str(value or "").strip()
    if (
        not raw
        or len(raw) > 2048
        or any(character.isspace() for character in raw)
        or any(unicodedata.category(character).startswith("C") for character in raw)
    ):
        return ""
    platform, separator, suffix = raw.partition(":")
    if not platform or not separator or not suffix:
        return ""
    canonical_platform = _normalize_content_history_source_platform(platform)
    if not canonical_platform:
        return ""
    normalized = f"{canonical_platform}:{suffix}"
    return normalized if len(normalized) <= 2048 else ""


def _content_history_item_key_platform(value: object) -> str:
    """Return the canonical platform prefix carried by an item key."""
    normalized = _normalize_content_history_item_key(value)
    platform, separator, suffix = normalized.partition(":")
    return platform if separator and suffix else ""


def _content_history_item_key_content_id(value: object) -> str:
    """Recover a raw stable ID from a canonical key when one is available."""
    normalized = _normalize_content_history_item_key(value)
    _platform, separator, suffix = normalized.partition(":")
    if not separator or not suffix or suffix.startswith("url:"):
        return ""
    return suffix


@dataclass(frozen=True)
class EventInsertResult:
    """One durable event insert/deduplication result."""

    event_id: int
    event_type: str
    ingest_key: str
    inserted: bool

    @property
    def duplicate(self) -> bool:
        """Whether an existing producer-owned row satisfied this write."""
        return not self.inserted


# Cap the probe so a long legitimate blurb never costs a full parse. Real
# copy is a sentence or two; a serialized batch payload is far longer than
# this, but the prefix alone is enough to identify one.
_LLM_PAYLOAD_PROBE_MAX_CHARS = 512
_LLM_PAYLOAD_MARKERS = ("expression", "topic_label", "reason", "topic_group")
_CARD_SETTLEMENT_REF_MAX_CHARS = 1024
_CARD_SETTLEMENT_EFFECT_NAMES = frozenset({"event"})
_CARD_SETTLEMENT_EFFECT_KEY_RE = re.compile(r"\Adialogue:[0-9a-f]{64}:[a-z_]+\Z")
_PROFILE_LEDGER_EFFECT_KEY_RE = re.compile(
    r"\Adialogue:[0-9a-f]{64}:(?:ledger|derived:[0-9a-f]{64})\Z"
)
# Aggregate-only temporal ranking shadows are tiny (one row per serve), so 30
# days / 5,000 rows retains several calibration windows without turning an
# observability feature into unbounded user-data growth.
_TEMPORAL_RANKING_AUDIT_RETENTION_DAYS = 30
_TEMPORAL_RANKING_AUDIT_MAX_ROWS = 5_000
_TEMPORAL_RANKING_AUDIT_POLICY = "temporal-ranking-shadow-v1"


def _validated_card_settlement_ref(ref: object) -> str:
    """Return a bounded, non-control settlement ref for stable-key derivation."""
    normalized = str(ref).strip()
    if (
        not normalized
        or len(normalized) > _CARD_SETTLEMENT_REF_MAX_CHARS
        or any(unicodedata.category(character).startswith("C") for character in normalized)
    ):
        raise ValueError("Card settlement ref is invalid")
    return normalized


def _card_settlement_effect_key(ref: object, effect: str) -> str:
    """Build one fixed-shape effect key without exposing SQL identifiers."""
    normalized_effect = effect.strip()
    if normalized_effect not in _CARD_SETTLEMENT_EFFECT_NAMES:
        raise ValueError(f"Unsupported card settlement effect: {effect!r}")
    normalized_ref = _validated_card_settlement_ref(ref)
    digest = hashlib.sha256(normalized_ref.encode("utf-8")).hexdigest()
    key = f"dialogue:{digest}:{normalized_effect}"
    if not _CARD_SETTLEMENT_EFFECT_KEY_RE.fullmatch(key):  # pragma: no cover - construction proof
        raise RuntimeError("Card settlement effect key construction failed")
    return key


def _validated_profile_ledger_effect_key(effect_key: object) -> str:
    """Accept only fixed-shape worker-generated keys; blank keeps legacy append mode."""
    normalized = str(effect_key or "").strip()
    if normalized and not _PROFILE_LEDGER_EFFECT_KEY_RE.fullmatch(normalized):
        raise ValueError("Profile ledger effect key is invalid")
    return normalized


def _looks_like_serialized_llm_payload(value: str) -> bool:
    """True when ``value`` is a serialized dict/list of LLM result fields.

    Prefix matching alone both over- and under-fires: legitimate copy may open
    with a code snippet, while a poisoned value may carry leading whitespace or
    use JSON rather than Python repr. Parse instead, and only reject when the
    result is a container carrying the fields the pipeline emits.
    """
    probe = value.strip()[:_LLM_PAYLOAD_PROBE_MAX_CHARS]
    if not probe.startswith(("{", "[")):
        return False
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(probe)
        except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError):
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if isinstance(item, dict) and any(marker in item for marker in _LLM_PAYLOAD_MARKERS):
                return True
    return False


@dataclass(frozen=True)
class PoolMaintenanceResult:
    """Observable outcome of one bounded recommendation-pool maintenance pass."""

    available_before: int
    available_after: int
    target: int
    protected_available: int
    recovered_suppressed: int
    trimmed_stale: int
    trimmed_explore_cluster: int
    trimmed_ready_reserve: int
    trimmed_evaluated: int
    trimmed_raw: int
    trimmed_by_source: dict[str, int]
    deferred_topic_trim: int
    deferred_source_trim: int
    deferred_stale_trim: int
    deferred_explore_cluster_trim: int
    raw_before: int
    raw_after: int
    raw_ceiling: int
    untrimmed_raw_excess: int
    rolled_back: bool
    reason: str = ""
    mutation_count: int = 0
    has_more: bool = False
    lock_wait_ms: float = 0.0
    recovery_ms: float = 0.0
    stale_trim_ms: float = 0.0
    explore_trim_ms: float = 0.0
    topic_trim_ms: float = 0.0
    source_trim_ms: float = 0.0
    raw_trim_ms: float = 0.0
    write_ms: float = 0.0
    total_ms: float = 0.0

    @property
    def at_target(self) -> bool:
        return self.available_after >= self.target


class PoolMaintenanceInvariantError(RuntimeError):
    """Raised when a maintenance transaction would violate availability."""


class PoolMaintenanceSnapshotUnavailableError(RuntimeError):
    """Raised when maintenance cannot acquire a canonical pre-change snapshot."""


class PoolMaintenanceDeferredError(PoolMaintenanceSnapshotUnavailableError):
    """Raised when interactive SQLite work owns the writer lock.

    Maintenance intentionally uses a short busy timeout. A lock collision is
    therefore a normal "try next tick" outcome rather than a 30-second wait.
    """


@dataclass(frozen=True)
class PoolServeSnapshot:
    """One consistent, connection-isolated read for the recommendation hot path."""

    readiness: dict[str, int]
    candidate_rows: tuple[dict[str, Any], ...]
    loaded_count: int
    platform_topups: tuple[tuple[str, int], ...]
    seen_bvids: frozenset[str]
    curator_signals: tuple[dict[str, Any], ...]
    feedback_signals: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class PoolPlatformAvailability:
    """Canonical servable inventory, split by source family.

    ``total_available`` always equals ``sum(by_platform.values())`` because
    both come from one isolated read of the same canonical available set
    (the one ``count_pool_candidates`` counts and ``serve()`` can load).
    Platforms holding nothing are absent from ``by_platform``; callers that
    need an explicit ``0`` supply it from their own enabled-source list.
    """

    total_available: int
    by_platform: dict[str, int]


@dataclass(frozen=True)
class PoolServePersistResult:
    """IDs committed by the recommendation hot path's isolated write."""

    recommendation_ids: tuple[int, ...]
    # ``None`` preserves compatibility with adapters that only return IDs.
    # The built-in SQLite path always returns the exact committed identities.
    committed_bvids: tuple[str, ...] | None = None
    temporally_stale_bvids: tuple[str, ...] = ()
    skipped_bvids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CacheContentWriteResult:
    """Authoritative outcome of one ``content_cache`` upsert.

    The temporal decision and final status are captured under the same SQLite
    writer lock as the row mutation.  Callers must use ``admitted`` instead of
    inferring success from physical row-count growth: an item can cross its
    TTL while waiting for that lock and be durably written as ``stale``.
    """

    bvid: str
    created: bool
    pool_status: str
    temporal_decision: TemporalEligibilityDecision

    @property
    def admitted(self) -> bool:
        """Whether this write left the item in the active fresh pool."""

        return self.temporal_decision.eligible and self.pool_status == "fresh"


@dataclass(frozen=True)
class _CacheContentPreparedWrite:
    """Parameters and evidence derived only after acquiring the writer lock."""

    params: tuple[Any, ...]
    bvid: str
    created: bool
    temporal_decision: TemporalEligibilityDecision
    renewed_review_retry_at: str = ""


@dataclass(frozen=True)
class _ContentTrimPlan:
    victim_bvids: tuple[str, ...] = ()
    deferred: int = 0


@dataclass(frozen=True)
class _RawTrimPlan:
    content_bvids: tuple[str, ...] = ()
    candidate_ids: tuple[int, ...] = ()
    candidate_statuses: tuple[tuple[int, str], ...] = ()
    untrimmed_excess: int = 0


# v0.3.62+: retry budget tightened from 5×100ms (worst-case 500ms
# blocking the asyncio event loop on lock contention) to 8×20ms
# (worst-case 160ms). Same total absolute timeout floor (~160-500ms)
# is preserved by raising attempt count; per-attempt sleep is short
# enough that even if it fires inside an async context the event-loop
# stutter is below human-perception thresholds. Most writes succeed
# on the first try anyway — this only matters under heavy concurrent
# write load (refresh tick + ingest + classify all hammering pool
# rows simultaneously). A future rewrite can move to asyncio.to_thread
# for true non-blocking DB I/O, but that's a larger refactor (every
# caller must become async) — for now this constant tweak is the
# pragmatic middle ground.
_LOCK_RETRY_ATTEMPTS = 8
_LOCK_RETRY_SLEEP_SECONDS = 0.02
CONTENT_HISTORY_RETENTION_DAYS = 30
_CONTENT_HISTORY_CATEGORIES = frozenset({"clicked", "shown", "removed"})
# CALIBRATION PROVENANCE: the recommendation endpoint has a 3s hard tail
# target. Recommendation persistence inherits the existing eight-attempt retry
# loop, so each attempt waits at most 250ms (about 2.14s including retry sleeps)
# rather than waiting 2.5s eight separate times. Maintenance waits only 75ms:
# it is retryable background work and must yield to user traffic instead of
# inheriting the process-wide 30s timeout. Revisit against production P99 lock
# telemetry if the endpoint SLO changes.
_INTERACTIVE_DB_BUSY_TIMEOUT_MS = 250
_MAINTENANCE_DB_BUSY_TIMEOUT_MS = 75
# CALIBRATION PROVENANCE: 25-50 rows was the rollout range proposed for the
# 100MB / 300-500 candidate production shape. Fifty amortizes ranked scans
# while bounding every write transaction; the async runner releases the lock
# and yields the loop between batches.
_POOL_MAINTENANCE_BATCH_SIZE = 50
_NATIVE_INTERNAL_RUNNER_PREFIX = "__openbiliclaw_"
_LEGACY_NATIVE_SAVE_RUNNER_ID = f"{_NATIVE_INTERNAL_RUNNER_PREFIX}legacy_runner__"
_EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS = {
    "youtube": "yt",
    "xiaohongshu": "xhs",
    "douyin": "dy",
    "twitter": "x",
    "zhihu": "zhihu",
    "reddit": "reddit",
}
_EXTENSION_NATIVE_SAVE_HOSTS = {
    "youtube": ("youtube.com", "youtu.be"),
    "xiaohongshu": ("xiaohongshu.com",),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "twitter": ("x.com", "twitter.com"),
    "zhihu": ("zhihu.com",),
    "reddit": ("reddit.com", "redd.it"),
}
_EXTENSION_NATIVE_SAVE_IDENTITY_QUERY_FIELDS = {
    "youtube": frozenset({"v"}),
    "xiaohongshu": frozenset({"xsec_token", "xsec_source"}),
}
_EXTENSION_NATIVE_SAVE_ACTIONS = frozenset({"favorite", "watch_later"})
_EXTENSION_NATIVE_SAVE_RESULT_STATUSES = frozenset(
    {"synced", "already_synced", "login_required", "rate_limited", "unsupported", "failed"}
)
_EXTENSION_NATIVE_SAVE_SLUGS = frozenset(_EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS.values())
_EXTENSION_NATIVE_SAVE_RESULT_MESSAGES = {
    ("synced", ""): "",
    ("already_synced", ""): "",
    ("login_required", ""): "Platform login required",
    ("rate_limited", ""): "Platform native save rate limited",
    ("unsupported", "unsupported_content_type"): (
        "Content type is unsupported for platform native save"
    ),
    ("failed", "native_save_failed"): "Platform native save failed",
    ("failed", "native_save_timeout"): "Platform native-save task timed out",
    ("failed", "native_content_not_ready"): "Platform content did not become ready",
    ("failed", "native_control_not_found"): "Platform save control was not found",
    ("failed", "native_dialog_not_opened"): "Platform save dialog did not open",
    ("failed", "native_target_not_found"): "Platform save target was not found uniquely",
    ("failed", "native_request_rejected"): "Platform native-save request was rejected",
    ("failed", "native_confirmation_not_observed"): (
        "Platform native-save confirmation was not observed"
    ),
}
_BVID_PATTERN = re.compile(r"(BV[0-9A-Za-z]+)")
_LOCAL_EVIDENCE_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# Event types that prove the user already consumed a piece of content, and so
# feed the durable seen ledger. ``view`` was the only member until 2026-07-26,
# which meant a video the user had explicitly favourited (or liked, or coined)
# but whose view event predated the history window could come back as a "new"
# recommendation. You cannot favourite something you never saw.
_SEEN_ITEM_EVENT_TYPES = frozenset({"view", "favorite", "like", "coin"})
_SEEN_ITEM_EVENT_TYPES_ORDERED = tuple(sorted(_SEEN_ITEM_EVENT_TYPES))
# Bump whenever _SEEN_ITEM_EVENT_TYPES grows, so existing installs rewind the
# backfill cursor once and pick up the newly eligible history.
_SEEN_ITEM_EVENT_TYPES_VERSION = 1
_KEYWORD_KIND_REGULAR = "regular"
_KEYWORD_KIND_EXPLORE = "explore"
_KEYWORD_KINDS = {_KEYWORD_KIND_REGULAR, _KEYWORD_KIND_EXPLORE}
_DISCOVERY_KEYWORD_METADATA_COLUMNS = {
    "aspect_id": "TEXT NOT NULL DEFAULT ''",
    "inspiration_backend": "TEXT NOT NULL DEFAULT ''",
    "inspiration_id": "TEXT NOT NULL DEFAULT ''",
    "inspiration_terms": "TEXT NOT NULL DEFAULT ''",
    "expansion_id": "TEXT NOT NULL DEFAULT ''",
    "expansion_label": "TEXT NOT NULL DEFAULT ''",
    "angle_id": "TEXT NOT NULL DEFAULT ''",
    "angle_label": "TEXT NOT NULL DEFAULT ''",
    "query_kind": "TEXT NOT NULL DEFAULT ''",
    "source_domain": "TEXT NOT NULL DEFAULT ''",
    "source_interest": "TEXT NOT NULL DEFAULT ''",
    "generation_reason": "TEXT NOT NULL DEFAULT ''",
    "normalized_keyword": "TEXT NOT NULL DEFAULT ''",
    "grounding_source": "TEXT NOT NULL DEFAULT ''",
}
# Yield-learning columns bolted onto ``discovery_inspiration_axis`` after the
# table shipped — added tolerantly via ADD COLUMN so pre-existing dbs upgrade
# in place (mirrors ``_DISCOVERY_KEYWORD_METADATA_COLUMNS``).
_DISCOVERY_INSPIRATION_AXIS_YIELD_COLUMNS = {
    "window_uses": "INTEGER NOT NULL DEFAULT 0",
    "yield_backfilled_at": "TEXT",
}
# discovery_keywords statuses meaning the keyword was actually leased for a
# fetch (it left 'pending'). 'pending' (never leased) and 'expired' (a stale
# digest superseded a still-pending row) were never consumed, so neither
# counts toward an axis's ``window_uses``. Locked against the status machine
# documented above ``insert_pending_keywords``.
_INSPIRATION_CONSUMED_KEYWORD_STATUSES = frozenset({"claimed", "executing", "used", "failed"})
_INSPIRATION_AXIS_ACTIVE_CAP = 16
_INSPIRATION_AXIS_EXPLORATION_PRIOR = 0.3
# Lifecycle thresholds (Phase 2 Part B). Retirement keys on the backfilled
# ``window_uses`` (keywords actually consumed), NOT the selection-bookkeeping
# ``use_count``: 5 consumption chances with a post-backfill score below 0.08
# (≈ zero admissions, e.g. 0.3/6 = 0.05) means the axis earned its exit.
_INSPIRATION_AXIS_RETIRE_MIN_WINDOW_USES = 5
_INSPIRATION_AXIS_RETIRE_YIELD_SCORE = 0.08
_INSPIRATION_AXIS_PURGE_AFTER_DAYS = 90
_INSPIRATION_AXIS_FRESHNESS_SCALE_DAYS = 30.0
_INSPIRATION_AXIS_KIND_ROTATION = (
    "subgenre",
    "creator_lens",
    "hands_on",
    "anchor",
    "community_vocab",
    "event",
    "method",
)
_INSPIRATION_AXIS_KIND_RANK = {
    axis_kind: index for index, axis_kind in enumerate(_INSPIRATION_AXIS_KIND_ROTATION)
}


def _normalize_keyword_kind(value: object) -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in _KEYWORD_KINDS else _KEYWORD_KIND_REGULAR


def _escape_like_term(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _local_evidence_tokens(query: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[\s,，。:：/|]+", query) if len(part.strip()) >= 2]
    if not parts:
        parts = [query]

    tokens: list[str] = []
    for part in parts:
        tokens.append(part)
        if len(part) >= 4 and _LOCAL_EVIDENCE_CJK_RE.search(part):
            tokens.extend(part[index : index + 2] for index in range(len(part) - 1))

    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return ordered


def _unique_clean_strings(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _json_array(values: Sequence[object] | None) -> str:
    return json.dumps(_unique_clean_strings(values or ()), ensure_ascii=False)


def _load_json_array(value: object) -> list[str]:
    if value is None:
        return []
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return _unique_clean_strings(loaded)


def _json_array_union(existing: object, incoming: Sequence[object]) -> str:
    return _json_array([*_load_json_array(existing), *incoming])


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_axis_datetime(value: object) -> datetime | None:
    from datetime import UTC, datetime

    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _axis_datetime_timestamp(value: object) -> float:
    parsed = _parse_axis_datetime(value)
    return parsed.timestamp() if parsed is not None else 0.0


def _axis_now_utc(now: datetime) -> datetime:
    from datetime import UTC

    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _axis_freshness(row: sqlite3.Row, now: datetime) -> float:
    refreshed_at = _parse_axis_datetime(row["last_refreshed_at"])
    if refreshed_at is None:
        return 0.0
    age_days = max(0.0, (_axis_now_utc(now) - refreshed_at).total_seconds() / 86400.0)
    return 1.0 / (1.0 + (age_days / _INSPIRATION_AXIS_FRESHNESS_SCALE_DAYS))


def _axis_kind_rank(value: object) -> int:
    return _INSPIRATION_AXIS_KIND_RANK.get(
        str(value or "").strip(),
        len(_INSPIRATION_AXIS_KIND_RANK),
    )


def _axis_effective_score(row: sqlite3.Row) -> float:
    """Return the ranking score with a *conditional* exploration prior floor.

    The prior only protects axes that have never been consumed
    (``window_uses == 0`` — genuine exploration). Once an axis has produced
    keywords that were consumed, it ranks on its real ``yield_score`` so a
    proven-bad axis (e.g. 5 uses / 0 admissions → 0.05) sinks below an unused
    one (0.3) instead of being floored back up to parity.
    """

    yield_score = _metric_float(row["yield_score"])
    if _metric_int(row["window_uses"]) > 0:
        return yield_score
    return max(yield_score, _INSPIRATION_AXIS_EXPLORATION_PRIOR)


def _axis_list_sort_key(row: sqlite3.Row, now: datetime) -> tuple[float, float, int, int, str]:
    score = _axis_freshness(row, now) * _axis_effective_score(row)
    return (
        -score,
        -_axis_datetime_timestamp(row["last_refreshed_at"]),
        _metric_int(row["use_count"]),
        _axis_kind_rank(row["axis_kind"]),
        str(row["axis_label"]),
    )


def _axis_cap_sort_key(row: sqlite3.Row) -> tuple[float, float, int, int, str]:
    return (
        -_axis_effective_score(row),
        -_axis_datetime_timestamp(row["last_refreshed_at"]),
        _metric_int(row["use_count"]),
        _axis_kind_rank(row["axis_kind"]),
        str(row["axis_label"]),
    )


def _axis_is_time_expired(row: sqlite3.Row, now: datetime) -> bool:
    if _metric_int(row["time_sensitive"]) <= 0:
        return False
    ttl = row["freshness_ttl_days"]
    if ttl is None:
        return False
    ttl_days = _metric_int(ttl)
    if ttl_days <= 0:
        return False
    refreshed_at = _parse_axis_datetime(row["last_refreshed_at"])
    if refreshed_at is None:
        return False
    age_seconds = (_axis_now_utc(now) - refreshed_at).total_seconds()
    return age_seconds > float(ttl_days) * 86400.0


def _attribute_inspiration_axis_id(
    *,
    angle_id: str,
    source_interest: str,
    angle_label: str,
    known_axis_ids: set[str],
) -> str | None:
    """Resolve a keyword row's owning axis id for yield attribution.

    ``angle_id`` is trusted only when it is a real axis (present in
    ``known_axis_ids``) — that guards against a legacy row whose ``angle_id``
    was set to its ``angle_label`` and merely looks id-shaped. Otherwise the id
    is re-derived from ``(source_interest, angle_label)``, matching how the axis
    itself hashes its id. Returns ``None`` when nothing is attributable.
    """

    if angle_id and angle_id in known_axis_ids:
        return angle_id
    if angle_label:
        return derive_inspiration_axis_id(source_interest, angle_label)
    return None


def _empty_interest_coverage() -> dict[str, object]:
    return {
        "generated_keyword_count": 0,
        "interest_selection_count": 0,
        "selected_keyword_count": 0,
        "candidate_count": 0,
        "candidate_share": 0.0,
        "admitted_count": 0,
        "yield_count": 0,
        "admitted_share": 0.0,
        "dominant_content_type": "",
        "dominant_content_type_share": 0.0,
        "dominant_candidate_platform": "",
        "dominant_candidate_platform_share": 0.0,
        "dominant_candidate_content_type": "",
        "dominant_candidate_content_type_share": 0.0,
        "last_interest_selected_at": "",
        "last_selected_at": "",
        "last_yielded_at": "",
    }


def _empty_keyword_cohort() -> dict[str, object]:
    return {
        "generated_keywords": 0,
        "claimed_keywords": 0,
        "claimed_rate": 0.0,
        "yield_attributed_admissions": 0,
        "admissions_per_claimed_keyword": 0.0,
        "mean_delight": 0.0,
        "distinct_topics": 0,
        "topic_diversity_per_100_admissions": 0.0,
        "claim_counts_by_day": {},
        "claim_counts_by_platform": {},
        "claim_counts_by_source_interest": {},
        "grounding_mix": {},
        "duplicate_rate_by_grounding_source": {},
    }


def _empty_interest_selection_report() -> dict[str, object]:
    return {
        "total_selected_interests": 0,
        "distinct_interests": 0,
        "by_source_interest": {},
        "by_query_kind": {},
        "last_selected_at": "",
    }


def _metric_int(value: object, default: int = 0) -> int:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError):
        return default


def _metric_float(value: object, default: float = 0.0) -> float:
    try:
        return float(cast("Any", value))
    except (TypeError, ValueError):
        return default


def _keyword_inspiration_gate(
    cohorts: dict[str, dict[str, object]],
    thresholds: Mapping[str, object],
    window_days: int,
) -> dict[str, object]:
    inspiration = cohorts.get("inspiration", {})
    merged = cohorts.get("merged", {})
    min_days = _metric_int(thresholds["min_window_days"])
    min_claimed = _metric_int(thresholds["min_inspiration_claimed_keywords"])
    claimed = _metric_int(inspiration.get("claimed_keywords", 0) or 0)
    checks = {
        "sample_floor": window_days >= min_days and claimed >= min_claimed,
        "admissions_per_claimed": False,
        "mean_delight": False,
        "topic_diversity": False,
    }
    if not checks["sample_floor"]:
        return {
            "verdict": "insufficient_sample",
            "checks": checks,
            "allowed_to_replace": False,
        }

    admission_ratio = _metric_float(thresholds["min_admissions_per_claimed_ratio"])
    delight_ratio = _metric_float(thresholds["min_mean_delight_ratio"])
    merged_admissions = _metric_float(merged.get("admissions_per_claimed_keyword", 0.0) or 0.0)
    merged_delight = _metric_float(merged.get("mean_delight", 0.0) or 0.0)
    merged_diversity = _metric_float(merged.get("topic_diversity_per_100_admissions", 0.0) or 0.0)
    inspiration_admissions = _metric_float(
        inspiration.get("admissions_per_claimed_keyword", 0.0) or 0.0
    )
    inspiration_delight = _metric_float(inspiration.get("mean_delight", 0.0) or 0.0)
    inspiration_diversity = _metric_float(
        inspiration.get("topic_diversity_per_100_admissions", 0.0) or 0.0
    )
    checks["admissions_per_claimed"] = inspiration_admissions >= merged_admissions * admission_ratio
    checks["mean_delight"] = inspiration_delight >= merged_delight * delight_ratio
    checks["topic_diversity"] = inspiration_diversity > merged_diversity
    allowed = all(bool(value) for value in checks.values())
    return {
        "verdict": "pass" if allowed else "fail",
        "checks": checks,
        "allowed_to_replace": allowed,
    }


def _metadata_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence):
        return ",".join(_unique_clean_strings(value))
    return str(value).strip()


def _normalized_keyword_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _keyword_matches_blocked_terms(value: object, blocked_terms: Sequence[str]) -> bool:
    """Conservatively match a keyword against current topic exclusions."""
    keyword = _normalized_keyword_text(value)
    if not keyword:
        return False
    ascii_keyword = f" {re.sub(r'[^0-9a-z]+', ' ', keyword).strip()} "
    compact_keyword = re.sub(r"[\W_]+", "", keyword)
    for raw_term in blocked_terms:
        term = _normalized_keyword_text(raw_term)
        if not term:
            continue
        if term.isascii():
            ascii_term = re.sub(r"[^0-9a-z]+", " ", term).strip()
            if ascii_term and f" {ascii_term} " in ascii_keyword:
                return True
            continue
        compact_term = re.sub(r"[\W_]+", "", term)
        if compact_term and compact_term in compact_keyword:
            return True
    return False


def _display_interest_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    chunk_size = max(1, int(size))
    return [list(values[index : index + chunk_size]) for index in range(0, len(values), chunk_size)]


# Mirrors recommendation.delight.DEFAULT_DELIGHT_THRESHOLD. Storage stays a
# leaf module (no openbiliclaw imports), so the value is duplicated here and
# pinned by tests/test_delight_scorer.py::test_delight_claim_threshold_floor_in_sync.
_DELIGHT_CLAIM_MIN_SCORE = 0.75
_DELIGHT_DYNAMIC_TOP_FRACTION = 0.10
_DELIGHT_DYNAMIC_MIN_SAMPLE_SIZE = 150
_DELIGHT_DYNAMIC_MIN_STDDEV = 0.08
_DELIGHT_SCORE_SYNC_EPSILON = 0.000001
_DEFAULT_ADMISSION_MIN_SCORE = DEFAULT_ADMISSION_MIN_SCORE


# A row cannot enter delight scoring until the same user-facing copy gate as
# the regular feed has completed. Keeping this predicate explicit prevents a
# relevance evaluator's internal ``reason`` from becoming either delight state
# or card copy while ``pool_expression`` is still pending.
def _delight_ready_copy_sql() -> str:
    return """
                      AND TRIM(COALESCE(pool_expression, '')) != ''
                      AND TRIM(COALESCE(pool_topic_label, '')) != ''
    """


def _delight_unseen_guard_sql() -> str:
    """Exclude canonical identities already present in the durable seen ledger."""
    return """
                      AND NOT EXISTS (
                        SELECT 1
                        FROM seen_items AS seen
                        WHERE seen.item_key = content_cache.item_key
                      )
    """


# Rows claimed by the surprise (delight) channel: already delivered as a
# delight, or scored above the current threshold with its formal pool copy
# synchronized into the delight snapshot. Requiring the exact snapshot keeps
# stale evaluator reasons from claiming rows and preserves the profile-aware
# scorer decision (for example the conservative 0.80 threshold).
def _delight_claim_guard_sql() -> str:
    return """
                  AND NOT (
                    COALESCE(delight_notified, 0) = 1
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND TRIM(COALESCE(pool_expression, '')) != ''
                      AND TRIM(COALESCE(pool_topic_label, '')) != ''
                      AND TRIM(COALESCE(delight_reason, '')) =
                          TRIM(COALESCE(pool_expression, ''))
                      AND TRIM(COALESCE(delight_hook, '')) =
                          TRIM(COALESCE(pool_topic_label, ''))
                    )
                  )
"""


_LEGACY_STYLE_KEY_MAP: dict[str, str] = {
    "deep_dive": "deep_focus",
    "tech_analysis": "deep_focus",
    "music_analysis": "deep_focus",
    "news_brief": "quick_scan",
    "practical_guide": "hands_on",
    "tutorial_short": "hands_on",
    "game_strategy": "hands_on",
    "review_roundup": "decision_support",
    "unboxing_experience": "decision_support",
    "story_doc": "story_immersion",
    "emotional_narrative": "story_immersion",
    "true_crime": "story_immersion",
    "opinion_stand": "opinion_sparring",
    "light_chat": "social_chat",
    "lifestyle": "daily_wander",
    "fun_variety": "mood_release",
    "parody_remix": "mood_release",
    "visual_showcase": "aesthetic_browse",
    "audio_background": "ambient_companion",
    "music_live": "live_pulse",
    "live_moment": "live_pulse",
    "sports_highlight": "live_pulse",
    "sci_fact": "curiosity_spark",
}

_EXPLORE_HIGH_RISK_CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "manufacturing",
        ("制造", "工艺", "工厂", "工业", "材料", "金属", "芯片", "显微", "纳米", "疲劳"),
    ),
    (
        "game_theory",
        ("博弈", "桌游", "纳什", "机制", "策略模型", "平衡性"),
    ),
)

# Schema version for migrations.  V4 adds the atomic temporal-evidence and
# review lifecycle fields to both discovery candidates and cached content; V6
# records the event source-attribution schema marker.
_SCHEMA_VERSION = 6

_SCHEMA_SQL = """
-- Event log (behavioral data from browser extension)
CREATE TABLE IF NOT EXISTS events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type            TEXT NOT NULL,        -- click, search, scroll, comment, etc.
    url                   TEXT,
    title                 TEXT,
    source_platform       TEXT NOT NULL DEFAULT '', -- canonical producer/platform tag
    content_id            TEXT NOT NULL DEFAULT '', -- stable source-content identity when known
    source_confidence     TEXT NOT NULL DEFAULT 'legacy_unknown',
    context               TEXT,                 -- JSON: DOM snapshot reference, viewport, etc.
    metadata              TEXT,                 -- JSON: additional event-specific data
    -- v0.3.x event-satisfaction signal: deterministic classification
    -- written at insert time by ``classify_event_satisfaction``. NULL on
    -- pre-migration rows; consumers treat NULL as ``unknown``.
    inferred_satisfaction TEXT,                 -- "positive" | "neutral" | "negative" | "unknown"
    satisfaction_reason   TEXT,                 -- short snake_case reason; see event_format.py
    ingest_key            TEXT NOT NULL DEFAULT '', -- producer-owned idempotency identity
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Durable source-aware identities extracted from every view event. This is
-- the recommendation/discovery hard-dedup source of truth; unlike the legacy
-- event scan it has no "latest N events" window.
CREATE TABLE IF NOT EXISTS seen_items (
    item_key        TEXT PRIMARY KEY,
    source_platform TEXT NOT NULL,
    content_id      TEXT NOT NULL,
    first_event_id  INTEGER NOT NULL,
    last_event_id   INTEGER NOT NULL,
    first_seen_at   TIMESTAMP NOT NULL,
    last_seen_at    TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_items_platform_content
    ON seen_items(source_platform, content_id);
CREATE TABLE IF NOT EXISTS seen_items_backfill_state (
    singleton             INTEGER PRIMARY KEY CHECK (singleton = 1),
    last_scanned_event_id INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO seen_items_backfill_state (singleton, last_scanned_event_id)
VALUES (1, 0);

-- Content cache (discovered/evaluated content)
CREATE TABLE IF NOT EXISTS content_cache (
    bvid        TEXT PRIMARY KEY,
    item_key    TEXT NOT NULL DEFAULT '',
    title       TEXT,
    up_name     TEXT,
    up_mid      INTEGER,
    duration    INTEGER,
    tags        TEXT,                 -- JSON array
    topic_key   TEXT DEFAULT '',
    style_key   TEXT DEFAULT '',
    franchise_key TEXT DEFAULT '',  -- LLM IP/series; see _ensure_content_cache_topic_columns
    description TEXT,
    published_at TEXT NOT NULL DEFAULT '',
    published_label TEXT NOT NULL DEFAULT '',
    cover_url   TEXT,
    view_count  INTEGER DEFAULT 0,
    like_count  INTEGER DEFAULT 0,
    favorite_count INTEGER DEFAULT 0,
    collect_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    danmaku_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    retweet_count INTEGER DEFAULT 0,
    bookmark_count INTEGER DEFAULT 0,
    rating_score REAL DEFAULT 0.0,
    rating_count INTEGER DEFAULT 0,
    source_rank INTEGER DEFAULT 0,
    relevance_score REAL DEFAULT 0.0,
    relevance_reason TEXT DEFAULT '',
    temporal_class TEXT DEFAULT 'unknown',
    temporal_confidence REAL DEFAULT 0.0,
    temporal_reason TEXT DEFAULT '',
    temporal_policy_version TEXT DEFAULT 'v1',
    temporal_validity_mode TEXT DEFAULT 'none',
    temporal_valid_until TEXT DEFAULT '',
    temporal_scope TEXT DEFAULT 'none',
    temporal_state TEXT DEFAULT 'unknown',
    temporal_evidence TEXT DEFAULT '',
    temporal_next_review_at TEXT DEFAULT '',
    temporal_evaluated_at TEXT DEFAULT '',
    temporal_evidence_complete INTEGER DEFAULT 0,
    temporal_review_attempts INTEGER DEFAULT 0,
    temporal_review_retry_at TEXT DEFAULT '',
    pool_expression TEXT DEFAULT '',
    pool_topic_label TEXT DEFAULT '',
    candidate_tier TEXT DEFAULT 'primary',
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notification_sent INTEGER DEFAULT 0,
    notified_at TIMESTAMP,
    pool_status TEXT DEFAULT 'fresh',
    recommended_at TIMESTAMP,
    feedback_type TEXT,
    feedback_at TIMESTAMP,
    source      TEXT,                -- Which discovery strategy found it
    body_text   TEXT DEFAULT '',     -- Full text body for text-first sources (X tweet/thread)
    content_type TEXT DEFAULT 'video',  -- Content shape: "video"|"note"|"tweet"|"thread"
    -- P1.8 yield provenance: discovery_keywords.id that produced this row;
    -- NULL for legacy / non-search / flag-off content.
    source_keyword_id INTEGER
);

-- Unified raw discovery candidate queue.
-- Producers enqueue platform-specific raw content here; evaluators claim
-- mixed-source batches and only accepted items advance into content_cache.
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key         TEXT NOT NULL UNIQUE,
    status                TEXT NOT NULL DEFAULT 'pending_eval',
    source_platform       TEXT NOT NULL DEFAULT '',
    source_strategy       TEXT NOT NULL DEFAULT '',
    source_context        TEXT NOT NULL DEFAULT '',
    content_type          TEXT NOT NULL DEFAULT 'video',
    body_text             TEXT NOT NULL DEFAULT '',
    bvid                  TEXT NOT NULL DEFAULT '',
    content_id            TEXT NOT NULL DEFAULT '',
    content_url           TEXT NOT NULL DEFAULT '',
    title                 TEXT NOT NULL DEFAULT '',
    author_name           TEXT NOT NULL DEFAULT '',
    up_name               TEXT NOT NULL DEFAULT '',
    up_mid                INTEGER NOT NULL DEFAULT 0,
    description           TEXT NOT NULL DEFAULT '',
    published_at          TEXT NOT NULL DEFAULT '',
    published_label       TEXT NOT NULL DEFAULT '',
    cover_url             TEXT NOT NULL DEFAULT '',
    duration              INTEGER NOT NULL DEFAULT 0,
    view_count            INTEGER NOT NULL DEFAULT 0,
    like_count            INTEGER NOT NULL DEFAULT 0,
    favorite_count        INTEGER NOT NULL DEFAULT 0,
    collect_count         INTEGER NOT NULL DEFAULT 0,
    comment_count         INTEGER NOT NULL DEFAULT 0,
    share_count           INTEGER NOT NULL DEFAULT 0,
    danmaku_count         INTEGER NOT NULL DEFAULT 0,
    reply_count           INTEGER NOT NULL DEFAULT 0,
    retweet_count         INTEGER NOT NULL DEFAULT 0,
    bookmark_count        INTEGER NOT NULL DEFAULT 0,
    rating_score          REAL NOT NULL DEFAULT 0.0,
    rating_count          INTEGER NOT NULL DEFAULT 0,
    source_rank           INTEGER NOT NULL DEFAULT 0,
    tags                  TEXT NOT NULL DEFAULT '[]',
    candidate_tier        TEXT NOT NULL DEFAULT 'primary',
    score_threshold       REAL NOT NULL DEFAULT 0.0,
    raw_payload           TEXT NOT NULL DEFAULT '{}',
    source_keyword_id     INTEGER,
    topic_key             TEXT NOT NULL DEFAULT '',
    topic_group           TEXT NOT NULL DEFAULT '',
    style_key             TEXT NOT NULL DEFAULT '',
    franchise_key         TEXT NOT NULL DEFAULT '',
    relevance_score       REAL NOT NULL DEFAULT 0.0,
    relevance_reason      TEXT NOT NULL DEFAULT '',
    temporal_class        TEXT NOT NULL DEFAULT 'unknown',
    temporal_confidence   REAL NOT NULL DEFAULT 0.0,
    temporal_reason       TEXT NOT NULL DEFAULT '',
    temporal_policy_version TEXT NOT NULL DEFAULT 'v1',
    temporal_validity_mode TEXT NOT NULL DEFAULT 'none',
    temporal_valid_until TEXT NOT NULL DEFAULT '',
    temporal_scope TEXT NOT NULL DEFAULT 'none',
    temporal_state TEXT NOT NULL DEFAULT 'unknown',
    temporal_evidence TEXT NOT NULL DEFAULT '',
    temporal_next_review_at TEXT NOT NULL DEFAULT '',
    temporal_evaluated_at TEXT NOT NULL DEFAULT '',
    temporal_evidence_complete INTEGER NOT NULL DEFAULT 0,
    temporal_review_attempts INTEGER NOT NULL DEFAULT 0,
    temporal_review_retry_at TEXT NOT NULL DEFAULT '',
    pool_expression       TEXT NOT NULL DEFAULT '',
    pool_topic_label      TEXT NOT NULL DEFAULT '',
    eval_error            TEXT NOT NULL DEFAULT '',
    eval_attempts         INTEGER NOT NULL DEFAULT 0,
    batch_eval_attempts   INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at            TIMESTAMP,
    claim_token           TEXT,
    evaluated_at          TIMESTAMP,
    cached_at             TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_status_seen
    ON discovery_candidates(status, last_seen_at, id);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source_status
    ON discovery_candidates(source_platform, status);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_content_id
    ON discovery_candidates(source_platform, content_id);
-- Bangumi anonymous API discovery pacing, cursors and diagnostics.
CREATE TABLE IF NOT EXISTS bangumi_discovery_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mode        TEXT NOT NULL,
    units       INTEGER NOT NULL DEFAULT 0,
    discovered  INTEGER NOT NULL DEFAULT 0,
    reason      TEXT NOT NULL DEFAULT 'ok',
    error_code  TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bangumi_runs_mode_created
    ON bangumi_discovery_runs(mode, created_at);
CREATE TABLE IF NOT EXISTS bangumi_discovery_state (
    state_key       TEXT PRIMARY KEY,
    cursor          INTEGER NOT NULL DEFAULT 0,
    total           INTEGER NOT NULL DEFAULT 0,
    cooldown_until  TEXT NOT NULL DEFAULT '',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Shared producer cadence ledger. Douyin / YouTube / X / Zhihu / Reddit used to
-- keep "when did I last run" in a Python attribute, so a backend restart wiped
-- the floor and the next tick fired immediately — measured on real data, 5 of
-- 55 Reddit rounds landed 8-40 minutes apart under a 60-minute setting. Rows
-- are written only for runs that actually produced candidates, which also stops
-- an empty run from burning a whole interval it should have retried inside.
CREATE TABLE IF NOT EXISTS source_producer_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    discovered  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_source_producer_runs_platform_created
    ON source_producer_runs(platform, created_at);

-- Recommendation history
CREATE TABLE IF NOT EXISTS recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT NOT NULL,
    item_key    TEXT NOT NULL DEFAULT '',
    expression  TEXT,                -- Friend-style recommendation text
    topic       TEXT,                -- Personal topic label
    confidence  REAL DEFAULT 0.0,
    presented   INTEGER DEFAULT 0,   -- Boolean
    feedback    TEXT,                -- User feedback (like/dislike/comment)
    feedback_type TEXT,
    feedback_note TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    presented_at TIMESTAMP,
    feedback_at TIMESTAMP,
    FOREIGN KEY (bvid) REFERENCES content_cache(bvid)
);

-- Durable popup chat turns.  These let the side panel recover in-flight
-- and completed replies after Chrome reloads or discards the panel page.
CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id       TEXT PRIMARY KEY,
    session       TEXT NOT NULL DEFAULT 'popup',
    scope         TEXT NOT NULL DEFAULT 'chat',
    subject_id    TEXT NOT NULL DEFAULT '',
    subject_title TEXT NOT NULL DEFAULT '',
    reply_to_turn_id TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    reply         TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '{}',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
    ON chat_turns(session, created_at, turn_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
    ON chat_turns(scope, subject_id, created_at);

-- Atomic winner receipt for durable dialogue-confirmation cards. Dialogue
-- effects are serialized by the single in-process settlement worker.
CREATE TABLE IF NOT EXISTS card_settlements (
    ref        TEXT PRIMARY KEY,
    verdict    TEXT NOT NULL,
    turn_id    TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    applied    INTEGER NOT NULL DEFAULT 0,
    result     TEXT NOT NULL DEFAULT '{}',
    event_id   TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Per-call LLM usage ledger. Populated by ``UsageRecorder`` after every
-- successful provider response. Used by ``openbiliclaw cost`` to print
-- daily spend summaries and by future per-module attribution work.
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    -- v0.3.28+: portion of prompt_tokens served from provider-side
    -- prompt cache. Always <= prompt_tokens. 0 means cache miss / no
    -- caching. Used to compute cache hit rate per caller.
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_cny REAL NOT NULL DEFAULT 0.0,
    success INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage(provider, model);

-- Phase 2 evaluator prefilter evidence. This table intentionally contains no
-- title, URL, author, prompt, profile text, or provider response. The shadow
-- decision is written before LLM I/O and joined to the eventual raw evaluator
-- score by ``decision_id``. Retention is enforced on every insert.
CREATE TABLE IF NOT EXISTS evaluator_prefilter_shadow_audit (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id              TEXT NOT NULL UNIQUE,
    candidate_hash           TEXT NOT NULL,
    platform_class           TEXT NOT NULL,
    context_class            TEXT NOT NULL,
    similarity               REAL,
    threshold                REAL NOT NULL,
    explore                  INTEGER NOT NULL DEFAULT 0,
    embedding_namespace      TEXT NOT NULL,
    profile_digest           TEXT NOT NULL,
    would_filter             INTEGER NOT NULL DEFAULT 0,
    embedding_status         TEXT NOT NULL,
    fail_open                INTEGER NOT NULL DEFAULT 0,
    explicit_strong_interest INTEGER NOT NULL DEFAULT 0,
    llm_score                REAL,
    admission_threshold      REAL,
    admission_result         INTEGER,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at             TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prefilter_shadow_created
    ON evaluator_prefilter_shadow_audit(created_at, id);
CREATE INDEX IF NOT EXISTS idx_prefilter_shadow_platform_context
    ON evaluator_prefilter_shadow_audit(platform_class, context_class, created_at);

-- Aggregate-only counterfactual for the recommendation temporal bonus. It
-- intentionally stores no content identity, title, URL, author, or keyword.
CREATE TABLE IF NOT EXISTS temporal_ranking_shadow_audit (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_version            TEXT NOT NULL,
    candidate_count           INTEGER NOT NULL,
    parseable_published_count INTEGER NOT NULL,
    bonus_eligible_count      INTEGER NOT NULL,
    class_counts_json         TEXT NOT NULL DEFAULT '{}',
    source_counts_json        TEXT NOT NULL DEFAULT '{}',
    age_bucket_counts_json    TEXT NOT NULL DEFAULT '{}',
    top_k_metrics_json        TEXT NOT NULL DEFAULT '[]',
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_temporal_ranking_shadow_created
    ON temporal_ranking_shadow_audit(created_at, id);

-- v0.3.174+ (cognitive profile pipeline Phase 0): append-only audit ledger
-- for profile write points. One row per profile-mutating action, recorded
-- AFTER the action finishes with its ``outcome`` (success|failed). The ledger
-- is a best-effort observer: a failed write here is logged at WARNING and
-- never blocks the underlying profile update. ``gate_verdict`` / ``held_id``
-- are reserved for later waves (posture gate shadow_* verdicts, confusion
-- held-update replay) and default empty in Wave A.
CREATE TABLE IF NOT EXISTS profile_update_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    effect_key     TEXT NOT NULL DEFAULT '',
    write_point    TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT '',
    before_summary TEXT NOT NULL DEFAULT '',
    after_summary  TEXT NOT NULL DEFAULT '',
    diff           TEXT NOT NULL DEFAULT '',
    source_refs    TEXT NOT NULL DEFAULT '',
    outcome        TEXT NOT NULL DEFAULT 'success',
    turn_id        TEXT NOT NULL DEFAULT '',
    gate_verdict   TEXT NOT NULL DEFAULT '',
    held_id        TEXT NOT NULL DEFAULT '',
    error          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_profile_ledger_timestamp
    ON profile_update_ledger(timestamp);
CREATE INDEX IF NOT EXISTS idx_profile_ledger_write_point
    ON profile_update_ledger(write_point, timestamp);

-- v0.3.175+ (cognitive profile pipeline Phase 2): "看不懂" (confusion) objects.
-- A confusion is raised when a behaviour cannot be cleanly read (awareness
-- source) or when a speculation expires in a stalemate (partial confirmation).
-- It never writes the profile directly; it only drives downstream ask/probe/wait
-- clarification and a topic-freeze reflex. The partial unique index enforces —
-- across ALL connections — that at most ONE confusion is in the ``clarifying``
-- state at any time (the durable ask budget), so the atomic claim is a single
-- ``UPDATE ... WHERE status='open'`` that the index rejects if another row
-- already holds the clarifying slot.
CREATE TABLE IF NOT EXISTS confusions (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status                    TEXT NOT NULL DEFAULT 'open',
    source                    TEXT NOT NULL DEFAULT '',
    topic                     TEXT NOT NULL DEFAULT '',
    observation               TEXT NOT NULL DEFAULT '',
    interpretation            TEXT NOT NULL DEFAULT '',
    interpretation_confidence REAL NOT NULL DEFAULT 0.0,
    evidence_refs             TEXT NOT NULL DEFAULT '[]',
    resolution                TEXT NOT NULL DEFAULT '',
    resolution_note           TEXT NOT NULL DEFAULT '',
    asked_at                  TIMESTAMP,
    ask_turn_id               TEXT NOT NULL DEFAULT '',
    defer_count               INTEGER NOT NULL DEFAULT 0,
    expires_at                TIMESTAMP,
    held_updates              TEXT NOT NULL DEFAULT '[]',
    replay_queue              TEXT NOT NULL DEFAULT '[]',
    resolved_at               TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_confusions_clarifying_unique
    ON confusions(status) WHERE status = 'clarifying';
CREATE INDEX IF NOT EXISTS idx_confusions_status
    ON confusions(status, updated_at);
"""


def _pool_source_family(source: object, source_platform: object = "") -> str:
    """Return the source family key used by pool share accounting."""
    return _source_family(source, source_platform)


def _normalize_source_platform_key(source_platform: object) -> str:
    """Return the canonical source key used in cross-source content IDs."""
    return normalize_source_platform(source_platform)


def _normalize_style_key_for_storage(value: object) -> str:
    """Canonicalize known style_key values while preserving unknown legacy rows."""
    token = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if not token:
        return ""
    return _LEGACY_STYLE_KEY_MAP.get(token, token)


def _is_linkable_pool_source(
    source: object,
    source_platform: object,
    content_url: object,
) -> bool:
    """Return False for xhs rows that cannot be opened from recommendations."""
    if _pool_source_family(source, source_platform) != _XHS_SOURCE_FAMILY:
        return True
    return "xsec_token=" in str(content_url or "")


def _xhs_self_author_guard_sql(table_alias: str = "content_cache") -> str:
    """Return a SQL AND clause that excludes self-authored XHS rows.

    The clause takes 3 positional ``?`` parameters (all the same nickname
    string). When the nickname is empty the clause is a no-op.
    """
    prefix = f"{table_alias}." if table_alias else ""
    return (
        "AND ("
        "? = '' "
        f"OR COALESCE({prefix}source_platform, '') != 'xiaohongshu' "
        "OR ("
        f"LOWER(COALESCE({prefix}up_name, '')) != LOWER(?) "
        f"AND LOWER(COALESCE({prefix}author_name, '')) != LOWER(?)"
        ")"
        ")"
    )


def _xhs_self_author_guard_params(xhs_self_nickname: str | None) -> tuple[str, str, str]:
    """Return the 3 bind values for ``_xhs_self_author_guard_sql``."""
    nickname = str(xhs_self_nickname or "").strip()
    return (nickname, nickname, nickname)


def _validated_extension_native_save_uuid(value: object, field_name: str) -> str:
    raw_text = str(value or "")
    if any(unicodedata.category(character).startswith("C") for character in raw_text):
        raise ValueError(f"{field_name} must not contain control characters")
    text = raw_text.strip()
    try:
        parsed = UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc
    if str(parsed) != text.lower():
        raise ValueError(f"{field_name} must be a canonical UUID")
    return text.lower()


def _validated_extension_native_save_text(
    value: object,
    field_name: str,
    *,
    max_length: int,
    allow_blank: bool = False,
) -> str:
    raw_text = str(value or "")
    if any(unicodedata.category(character).startswith("C") for character in raw_text):
        raise ValueError(f"{field_name} must not contain control characters")
    text = raw_text.strip()
    if not text and not allow_blank:
        raise ValueError(f"{field_name} must not be blank")
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return text


def _canonical_extension_native_save_url(platform: str, value: object) -> str:
    raw_url = _validated_extension_native_save_text(value, "content_url", max_length=2048)
    try:
        parts = urlsplit(raw_url)
        port = parts.port
    except ValueError as exc:
        raise ValueError("content_url must use an allow-listed platform HTTPS host") from exc
    hostname = (parts.hostname or "").lower().rstrip(".")
    allowed_hosts = _EXTENSION_NATIVE_SAVE_HOSTS[platform]
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or not any(hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts)
    ):
        raise ValueError("content_url must use an allow-listed platform HTTPS host")
    retained_fields = _EXTENSION_NATIVE_SAVE_IDENTITY_QUERY_FIELDS.get(platform, frozenset())
    retained_query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key in retained_fields
    ]
    if platform == "youtube" and retained_query:
        videos = [item for key, item in retained_query if key == "v"]
        if len(videos) != 1 or not videos[0]:
            raise ValueError("content_url has an invalid YouTube navigation query")
    if platform == "xiaohongshu" and retained_query:
        seen_fields: set[str] = set()
        for key, item in retained_query:
            if key in seen_fields or not item:
                raise ValueError("content_url has an invalid Xiaohongshu navigation query")
            seen_fields.add(key)
        if "xsec_token" not in seen_fields:
            raise ValueError("content_url has an invalid Xiaohongshu navigation query")
    query = urlencode(retained_query)
    return urlunsplit(("https", hostname, parts.path or "/", query, ""))


def _validated_extension_native_save_job(
    job: ExtensionNativeSaveJob,
) -> dict[str, str]:
    job_id = _validated_extension_native_save_uuid(job.job_id, "job_id")
    platform = canonical_source_platform(job.platform)
    if platform != job.platform or platform not in _EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS:
        raise ValueError("platform must be a supported canonical platform")
    platform_slug = _validated_extension_native_save_text(
        job.platform_slug, "platform_slug", max_length=16
    ).lower()
    if platform_slug != _EXTENSION_NATIVE_SAVE_PLATFORM_SLUGS[platform]:
        raise ValueError("platform_slug does not match platform")
    content_id = _validated_extension_native_save_text(job.content_id, "content_id", max_length=512)
    content_url = _canonical_extension_native_save_url(platform, job.content_url)
    item_key = _validated_extension_native_save_text(job.item_key, "item_key", max_length=768)
    if item_key != make_item_key(platform, content_id, content_url):
        raise ValueError("item_key does not match the canonical platform identity")
    content_type = _validated_extension_native_save_text(
        job.content_type, "content_type", max_length=128
    )
    requested_action = _validated_extension_native_save_text(
        job.requested_action, "requested_action", max_length=32
    )
    resolved_action = _validated_extension_native_save_text(
        job.resolved_action, "resolved_action", max_length=32
    )
    if requested_action not in _EXTENSION_NATIVE_SAVE_ACTIONS:
        raise ValueError("requested_action is invalid")
    if resolved_action not in _EXTENSION_NATIVE_SAVE_ACTIONS:
        raise ValueError("resolved_action is invalid")
    target_label = _validated_extension_native_save_text(
        job.target_label, "target_label", max_length=256
    )
    return {
        "job_id": job_id,
        "platform": platform,
        "platform_slug": platform_slug,
        "item_key": item_key,
        "content_id": content_id,
        "content_url": content_url,
        "content_type": content_type,
        "requested_action": requested_action,
        "resolved_action": resolved_action,
        "target_label": target_label,
    }


def _validated_extension_native_save_result(
    status: object, error_code: object, error_message: object
) -> tuple[str, str, str]:
    safe_status = _validated_extension_native_save_text(status, "status", max_length=32)
    if safe_status not in _EXTENSION_NATIVE_SAVE_RESULT_STATUSES:
        raise ValueError("status is invalid")
    safe_code = _validated_extension_native_save_text(
        error_code, "error_code", max_length=128, allow_blank=True
    )
    _validated_extension_native_save_text(
        error_message, "error_message", max_length=512, allow_blank=True
    )
    try:
        safe_message = _EXTENSION_NATIVE_SAVE_RESULT_MESSAGES[(safe_status, safe_code)]
    except KeyError as exc:
        raise ValueError("status and error_code combination is invalid") from exc
    return safe_status, safe_code, safe_message


def _normalize_admission_min_score(value: object) -> float:
    if isinstance(value, bool):
        return _DEFAULT_ADMISSION_MIN_SCORE
    if not isinstance(value, (int, float, str)):
        return _DEFAULT_ADMISSION_MIN_SCORE
    try:
        score = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_ADMISSION_MIN_SCORE
    if score <= 0.0 or score > 1.0:
        return _DEFAULT_ADMISSION_MIN_SCORE
    return score


def _normalize_temporal_fields_for_storage(
    *,
    temporal_class: object,
    temporal_confidence: object,
    temporal_reason: object,
    temporal_policy_version: object | None = None,
) -> tuple[str, float, str, str]:
    """Validate temporal evaluator metadata before it reaches SQLite."""

    normalized_class = normalize_temporal_class(temporal_class)
    try:
        normalized_confidence = normalize_temporal_confidence(temporal_confidence)
    except (OverflowError, TypeError, ValueError):
        normalized_confidence = 0.0
    raw_confidence: float | None = None
    if not isinstance(temporal_confidence, bool) and isinstance(temporal_confidence, int | float):
        try:
            raw_confidence = float(temporal_confidence)
        except (OverflowError, TypeError, ValueError):
            raw_confidence = None
    confidence_is_valid = (
        raw_confidence is not None
        and math.isfinite(raw_confidence)
        and 0.0 <= raw_confidence <= 1.0
    )
    normalized_reason = temporal_reason.strip() if isinstance(temporal_reason, str) else ""
    reason_is_valid = bool(normalized_reason)
    is_neutral_default = (
        normalized_class == "unknown"
        and isinstance(temporal_class, str)
        and temporal_class.strip().lower() == "unknown"
        and confidence_is_valid
        and normalized_confidence == 0.0
        and isinstance(temporal_reason, str)
        and not normalized_reason
    )
    if normalized_class == "unknown" or not confidence_is_valid or not reason_is_valid:
        if not is_neutral_default:
            logger.warning(
                "Invalid temporal metadata coerced to unknown "
                "(class_type=%s, confidence_type=%s, reason_type=%s)",
                type(temporal_class).__name__,
                type(temporal_confidence).__name__,
                type(temporal_reason).__name__,
            )
        policy_version = (
            TEMPORAL_POLICY_VERSION
            if str(temporal_policy_version or "").strip().lower() == TEMPORAL_POLICY_VERSION
            else "v1"
        )
        return ("unknown", 0.0, "", policy_version)
    policy_version = (
        TEMPORAL_POLICY_VERSION
        if str(temporal_policy_version or "").strip().lower() == TEMPORAL_POLICY_VERSION
        else "v1"
    )
    return (
        normalized_class,
        normalized_confidence,
        normalized_reason,
        policy_version,
    )


def _publication_datetime_for_merge(value: object) -> datetime | None:
    """Parse stored canonical publication evidence without applying a future cap."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _merge_publication_evidence_conservatively(
    *,
    existing_at: object,
    existing_label: object,
    incoming_at: object,
    incoming_label: object,
) -> tuple[str, str]:
    """Keep the earliest trustworthy timestamp and its matching label.

    Once an evaluator has attached non-neutral temporal semantics, allowing a
    later raw timestamp to replace an older publication clock can resurrect an
    expired item.  Earlier corrections are safe and deliberately win.  A label
    follows the timestamp it described instead of being merged independently.
    """

    existing_value = str(existing_at or "").strip()
    incoming_value = str(incoming_at or "").strip()
    existing_text = str(existing_label or "").strip()
    incoming_text = str(incoming_label or "").strip()
    if not existing_value:
        return incoming_value, incoming_text
    if not incoming_value:
        return existing_value, existing_text
    existing_clock = _publication_datetime_for_merge(existing_value)
    incoming_clock = _publication_datetime_for_merge(incoming_value)
    if existing_clock is None:
        return (
            (incoming_value, incoming_text)
            if incoming_clock is not None
            else (
                existing_value,
                existing_text,
            )
        )
    if incoming_clock is None or existing_clock < incoming_clock:
        return existing_value, existing_text
    if incoming_clock < existing_clock:
        return incoming_value, incoming_text
    return existing_value, existing_text or incoming_text


_TEMPORAL_LIFECYCLE_FIELDS = (
    "temporal_validity_mode",
    "temporal_valid_until",
    "temporal_scope",
    "temporal_state",
    "temporal_evidence",
    "temporal_next_review_at",
    "temporal_evaluated_at",
    "temporal_evidence_complete",
)


def _temporal_lifecycle_group_for_storage(value: Mapping[str, Any]) -> tuple[Any, ...]:
    """Normalize the v2 temporal evidence fields as one indivisible group."""

    return (
        str(value.get("temporal_validity_mode", "none") or "none").strip().lower(),
        str(value.get("temporal_valid_until", "") or "").strip(),
        str(value.get("temporal_scope", "none") or "none").strip().lower(),
        str(value.get("temporal_state", "unknown") or "unknown").strip().lower(),
        str(value.get("temporal_evidence", "") or "").strip(),
        str(value.get("temporal_next_review_at", "") or "").strip(),
        str(value.get("temporal_evaluated_at", "") or "").strip(),
        int(is_complete_temporal_evidence_marker(value.get("temporal_evidence_complete"))),
    )


def _parse_temporal_storage_clock(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_temporal_evidence_group_for_storage(
    value: Mapping[str, Any],
    *,
    content_text: object | None = None,
) -> tuple[tuple[str, float, str, str], tuple[Any, ...]]:
    """Validate v2 model evidence atomically, while retaining v1 rows."""

    policy_version = str(value.get("temporal_policy_version") or "").strip().lower()
    if policy_version != TEMPORAL_POLICY_VERSION:
        temporal = _normalize_temporal_fields_for_storage(
            temporal_class=value.get("temporal_class", "unknown"),
            temporal_confidence=value.get("temporal_confidence", 0.0),
            temporal_reason=value.get("temporal_reason", ""),
            temporal_policy_version=policy_version or None,
        )
        return temporal, (
            "none",
            "",
            "none",
            "unknown",
            "",
            "",
            "",
            int(is_complete_temporal_evidence_marker(value.get("temporal_evidence_complete"))),
        )

    parsed = parse_temporal_evaluation(value)
    if content_text is not None:
        # Sink-side defense: an in-process/manual caller cannot turn a model
        # claim into hard expiry merely by setting ``evidence_complete``. The
        # exact excerpt must still exist in the same prompt-visible fields as
        # the evaluator path. Existing durable groups pass ``None`` here and
        # are preserved as already-validated evidence.
        parsed = ground_temporal_evaluation(parsed, content_text=content_text)
    evaluated_at = str(value.get("temporal_evaluated_at") or "").strip()
    evaluated_clock = _parse_temporal_storage_clock(evaluated_at) if evaluated_at else None
    try:
        evaluated_is_trusted = bool(
            evaluated_clock is not None
            and evaluated_clock <= datetime.now(UTC) + PUBLICATION_CLOCK_SKEW_TOLERANCE
        )
    except (OverflowError, OSError, ValueError):
        evaluated_is_trusted = False
    complete = is_complete_temporal_evidence_marker(
        value.get("temporal_evidence_complete")
    ) and bool(parsed.evidence_complete)
    if not complete or not evaluated_is_trusted:
        return (
            ("unknown", 0.0, "", TEMPORAL_POLICY_VERSION),
            ("none", "", "none", "unknown", "", "", "", 0),
        )

    # Review clocks are policy-owned, not evaluator/caller-owned.  Recompute
    # them from the validated evidence and evaluation clock at every storage
    # boundary so a malformed adapter cannot postpone a required review to an
    # arbitrary date (for example 2099). Existing rows are canonicalized by
    # the same rule when they next pass through an admission decision.
    scheduled = schedule_temporal_evaluation(parsed, evaluated_at=evaluated_at)
    if not scheduled.evidence_complete or not scheduled.temporal_evaluated_at:
        return (
            ("unknown", 0.0, "", TEMPORAL_POLICY_VERSION),
            ("none", "", "none", "unknown", "", "", "", 0),
        )
    return (
        (
            scheduled.temporal_class,
            scheduled.temporal_confidence,
            scheduled.temporal_reason,
            TEMPORAL_POLICY_VERSION,
        ),
        (
            scheduled.temporal_validity_mode,
            scheduled.temporal_valid_until,
            scheduled.temporal_scope,
            scheduled.temporal_state,
            scheduled.temporal_evidence,
            scheduled.temporal_next_review_at,
            scheduled.temporal_evaluated_at,
            1,
        ),
    )


def _temporal_grounding_was_downgraded(
    value: Mapping[str, Any],
    *,
    content_text: object,
) -> bool:
    """Return whether grounding weakened an otherwise complete active claim."""

    parsed = parse_temporal_evaluation(value)
    if not parsed.evidence_complete or parsed.temporal_validity_mode not in {
        "explicit_deadline",
        "event_state",
        "version_state",
        "freshness_only",
    }:
        return False
    grounded = ground_temporal_evaluation(parsed, content_text=content_text)
    return grounded.temporal_validity_mode != parsed.temporal_validity_mode


def _temporal_evidence_is_authoritative_replacement(
    temporal: tuple[str, float, str, str],
    lifecycle: tuple[Any, ...],
) -> bool:
    """Return whether new evidence may replace an existing classification.

    A low-confidence or hook-only rereview is useful metadata on a new item,
    but it cannot release an item held by older high-confidence core evidence.
    Durable evergreen/historical conclusions are the one legitimate
    scope-less replacement because their contract is precisely ``none``.
    """

    temporal_class, confidence, _reason, policy_version = temporal
    mode, _until, scope, _state, _evidence, _review_at, _evaluated_at, complete = lifecycle
    if (
        policy_version != TEMPORAL_POLICY_VERSION
        or temporal_class == "unknown"
        or confidence < TEMPORAL_CONFIDENCE_FULL
        or not bool(complete)
    ):
        return False
    normalized_mode = str(mode)
    normalized_scope = str(scope)
    if temporal_class in {"evergreen", "historical"}:
        return normalized_mode == "none" and normalized_scope == "none"
    return normalized_scope == "core" and normalized_mode != "none"


def _temporal_review_retry_hours(attempts: int) -> int:
    """Return the bounded retry delay for an already-counted review attempt."""

    exponent = min(max(0, int(attempts) - 1), 5)
    return int(min(24, 2**exponent))


def _temporal_review_retry_text(*, now: datetime, attempts: int) -> str:
    return (
        (now + timedelta(hours=_temporal_review_retry_hours(attempts)))
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


_TEMPORAL_REVIEW_RETRY_REFRESH_ASSIGNMENT_SQL = """
temporal_review_retry_at = CASE
    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
     AND (
        COALESCE(temporal_review_retry_at, '') = ''
        OR julianday(temporal_review_retry_at) <= julianday('now')
     )
    THEN datetime(
        'now',
        '+' || CASE
            WHEN COALESCE(temporal_review_attempts, 0) <= 1 THEN 1
            WHEN temporal_review_attempts = 2 THEN 2
            WHEN temporal_review_attempts = 3 THEN 4
            WHEN temporal_review_attempts = 4 THEN 8
            WHEN temporal_review_attempts = 5 THEN 16
            ELSE 24
        END || ' hours'
    )
    ELSE COALESCE(temporal_review_retry_at, '')
END
""".strip()


def _temporal_decision_disposition(decision: TemporalEligibilityDecision) -> str:
    """Return the v2 three-state result while preserving v1 compatibility."""

    disposition = str(getattr(decision, "disposition", "") or "").strip().lower()
    if disposition in {"eligible", "review_due", "expired"}:
        return disposition
    return "eligible" if decision.eligible else "expired"


def _pool_status_for_temporal_decision(decision: TemporalEligibilityDecision) -> str:
    disposition = _temporal_decision_disposition(decision)
    if disposition == "expired":
        return "stale"
    if disposition == "review_due":
        return "temporal_review_hold"
    return "fresh"


class Database:
    """Lightweight SQLite wrapper for OpenBiliClaw.

    Manages the event log, content cache, and recommendation history.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        # ``check_same_thread=False`` permits a connection to move between
        # threads; it does not make simultaneous ``execute`` / ``fetch`` calls
        # safe.  FastAPI status reads and database-owned workers run outside the
        # event-loop thread, so give every thread its own WAL connection instead
        # of blocking the loop on one process-wide mutex.  Coroutines on the
        # event-loop thread cannot interleave inside these synchronous methods.
        self._connection_registry_lock = threading.RLock()
        self._connection_owner_thread_id: int | None = None
        self._connections_by_thread: dict[int, sqlite3.Connection] = {}
        self._admission_min_score = _DEFAULT_ADMISSION_MIN_SCORE
        # RuntimeContext updates this immutable policy when configuration is
        # applied. None preserves the historical platform-neutral pool gate.
        self._publication_date_preference: object | None = None
        # Per-source publication-date preferences for raw candidate enqueue
        # filtering (discovery_candidates). None / empty = platform-neutral.
        self._source_publication_date_preferences: dict[str, Any] | None = None
        self._preserve_read_transaction = False
        # The two queues must remain separate: a slow/background maintenance
        # batch must never sit in front of an interactive recommendation read.
        # Executors are lazy so short-lived CLI/tests that never use async DB
        # work do not create threads.
        self._worker_init_lock = threading.Lock()
        self._maintenance_executor: ThreadPoolExecutor | None = None
        self._serve_executor: ThreadPoolExecutor | None = None
        # The durable seen-item ledger avoids reparsing arbitrary event-history
        # windows on recommendation reads. Cache immutable identity snapshots by
        # the newest ledger event id and share them with isolated workers.
        self._seen_state_lock = threading.Lock()
        self._seen_state_cache: dict[
            int,
            tuple[frozenset[str], frozenset[str]],
        ] = {}

    def set_admission_min_score(self, value: object) -> None:
        """Set the unified recommendation-pool admission floor."""
        self._admission_min_score = _normalize_admission_min_score(value)

    def set_publication_date_preference(self, preference: object | None) -> None:
        """Set the current Bilibili publication policy used by pool reads."""
        self._publication_date_preference = preference

    def set_source_publication_date_preferences(
        self,
        preferences: dict[str, Any] | None,
    ) -> None:
        """Set the per-source publication policy used by raw candidate enqueue."""
        if preferences is None:
            self._source_publication_date_preferences = None
        elif isinstance(preferences, dict):
            self._source_publication_date_preferences = dict(preferences)
        else:
            self._source_publication_date_preferences = None

    def _source_publication_date_candidate_is_eligible(
        self,
        source_platform: str,
        published_at: object,
    ) -> bool:
        """Apply a source date preference before a raw candidate is enqueued."""
        preferences = self._source_publication_date_preferences
        if not preferences:
            return True
        preference = preferences.get(source_platform)
        if preference is None:
            return True
        try:
            from openbiliclaw.recommendation.publication_preference import (
                evaluate_source_publication_preference,
            )

            return evaluate_source_publication_preference(
                published_at=published_at,
                preference=preference,
            ).in_range
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid publication preference during candidate enqueue",
                exc_info=True,
            )
            return True

    def _publication_date_row_is_eligible(self, row: Mapping[str, Any]) -> bool:
        """Apply strict Bilibili date preference without deleting stored rows."""
        preference = self._publication_date_preference
        if preference is None:
            return True
        try:
            from openbiliclaw.recommendation.publication_preference import (
                evaluate_publication_preference,
            )

            decision = evaluate_publication_preference(
                # Older pool rows may have an empty platform column; derive
                # their canonical family from the persisted source strategy.
                source_platform=(
                    row.get("source_platform")
                    or _pool_source_family(row.get("source"), row.get("source_platform"))
                ),
                published_at=row.get("published_at", ""),
                preference=cast("PublicationDatePreference", preference),
            )
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid publication preference in pool read", exc_info=True)
            return True
        return decision.eligible

    def initialize(self) -> None:
        """Initialize the database and run migrations if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve the long-lived facade connection's legacy schema semantics:
        # foreign-key enforcement has historically been enabled only for
        # explicit transaction connections.  Turning it on here would
        # make unrelated recommendation writes fail after this concurrency fix.
        self._conn = self._new_connection(enforce_foreign_keys=False)
        owner_thread_id = threading.get_ident()
        with self._connection_registry_lock:
            self._connection_owner_thread_id = owner_thread_id
            self._connections_by_thread = {owner_thread_id: self._conn}
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._ensure_event_source_columns()
        self._ensure_event_satisfaction_columns()
        self._ensure_event_ingest_key_column()
        self._ensure_recommendation_feedback_columns()
        self._ensure_content_cache_runtime_columns()
        self._ensure_content_cache_relevance_columns()
        self._ensure_content_cache_temporal_columns()
        self._ensure_content_cache_topic_columns()
        self._ensure_content_cache_pool_copy_columns()
        self._ensure_content_cache_delight_columns()
        self._ensure_content_cache_keyframe_columns()
        self._ensure_content_cache_danmaku_columns()
        self._ensure_content_cache_multisource_columns()
        self._ensure_content_identity_columns()
        self._ensure_seen_items_ledger()
        self._ensure_content_cache_quality_columns()
        self._ensure_recommendation_read_indexes()
        self._ensure_source_recipes_table()
        self._ensure_xhs_observed_urls_table()
        self._ensure_discovery_candidate_columns()
        self._normalize_legacy_style_keys()
        self._ensure_llm_usage_cache_columns()
        self._ensure_chat_turns_table()
        self._ensure_profile_update_ledger_table()
        self._ensure_confusions_table()
        self._ensure_watch_later_table()
        self._ensure_discovery_keywords_table()
        self._ensure_favorites_table()
        self._ensure_saved_sync_tables()
        self._ensure_content_history_tables()
        self._ensure_user_visual_clusters_table()
        self._ensure_auth_state_table()
        self._ensure_init_runs_table()
        self.reset_stale_discovery_candidate_evaluations()
        self.suppress_low_score_pool_items()
        self.suppress_low_confidence_recommendations()

        # Set schema version
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
        self._conn.commit()
        logger.info("Database initialized at %s", self._db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        primary = self._conn
        if primary is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        thread_id = threading.get_ident()
        with self._connection_registry_lock:
            # Lightweight wrappers used by isolated reads and a few tests bind
            # ``_conn`` directly instead of calling ``initialize``.  The first
            # accessor becomes that connection's owning thread.
            if self._connection_owner_thread_id is None:
                self._connection_owner_thread_id = thread_id
                self._connections_by_thread[thread_id] = primary
                return primary
            if thread_id == self._connection_owner_thread_id:
                return primary
            connection = self._connections_by_thread.get(thread_id)
            if connection is None:
                connection = self._new_connection(enforce_foreign_keys=False)
                self._connections_by_thread[thread_id] = connection
            return connection

    def _new_connection(
        self,
        *,
        enforce_foreign_keys: bool,
    ) -> sqlite3.Connection:
        """Open one configured connection for exactly one runtime thread."""
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        # SQL projections must use the same platform alias registry as Python
        # ingestion.  Register the shared helper instead of copying aliases
        # into CASE expressions that would drift as platforms evolve.
        connection.create_function(
            "normalize_source_platform",
            1,
            _normalize_content_history_source_platform,
            deterministic=True,
        )
        connection.create_function(
            "normalize_content_history_item_key",
            1,
            _normalize_content_history_item_key,
            deterministic=True,
        )
        connection.create_function(
            "content_history_item_key_platform",
            1,
            _content_history_item_key_platform,
            deterministic=True,
        )
        connection.create_function(
            "content_history_item_key_content_id",
            1,
            _content_history_item_key_content_id,
            deterministic=True,
        )
        if enforce_foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _pool_admission_min_score(self) -> float:
        return _normalize_admission_min_score(self._admission_min_score)

    def pool_admission_threshold(
        self,
        source_strategy: object,
        requested_threshold: object | None = None,
    ) -> float:
        """Return the shared effective admission floor for one source."""
        return effective_admission_threshold(
            source_strategy,
            self._pool_admission_min_score(),
            requested_threshold,
        )

    def _pool_admission_sql(
        self,
        *,
        score_expr: str = "COALESCE(relevance_score, 0.0)",
        source_expr: str = "source",
    ) -> tuple[str, tuple[Any, ...]]:
        """Return a SQL predicate and params for the shared admission policy."""
        predicate = f"""
            {score_expr} >= CASE
                WHEN LOWER(TRIM(COALESCE({source_expr}, ''))) = ? THEN ?
                ELSE ?
            END
        """
        return predicate, (
            EXPLORE_STRATEGY,
            EXPLORE_ADMISSION_MIN_SCORE,
            self._pool_admission_min_score(),
        )

    def open_connection(self) -> sqlite3.Connection:
        """Open a short-lived connection to the initialized database.

        Use this for explicit transactions that may run from FastAPI's
        threadpool. A separate connection lets SQLite serialize writers
        with ``busy_timeout`` instead of nesting transactions on the
        process-wide connection.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._new_connection(enforce_foreign_keys=True)

    def _executor(self, *, maintenance: bool) -> ThreadPoolExecutor:
        """Return the lazily-created single-thread SQLite worker."""
        executor = self._maintenance_executor if maintenance else self._serve_executor
        if executor is not None:
            return executor
        with self._worker_init_lock:
            executor = self._maintenance_executor if maintenance else self._serve_executor
            if executor is None:
                prefix = (
                    "openbiliclaw-pool-maintenance" if maintenance else "openbiliclaw-pool-serve"
                )
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=prefix)
                if maintenance:
                    self._maintenance_executor = executor
                else:
                    self._serve_executor = executor
        return executor

    async def maintain_pool_inventory_async(self, **kwargs: Any) -> PoolMaintenanceResult:
        """Run one bounded maintenance batch on its dedicated worker."""
        loop = asyncio.get_running_loop()
        executor = self._executor(maintenance=True)
        return await loop.run_in_executor(executor, partial(self.maintain_pool_inventory, **kwargs))

    async def load_pool_serve_snapshot_async(
        self,
        *,
        limit: int,
        xhs_self_nickname: str = "",
        curator_history_limit: int = 30,
        source_platform: str = "",
    ) -> PoolServeSnapshot:
        """Load a recommendation snapshot on the interactive SQLite worker."""
        loop = asyncio.get_running_loop()
        executor = self._executor(maintenance=False)
        return await loop.run_in_executor(
            executor,
            partial(
                self.load_pool_serve_snapshot,
                limit=limit,
                xhs_self_nickname=xhs_self_nickname,
                curator_history_limit=curator_history_limit,
                source_platform=source_platform,
            ),
        )

    async def persist_pool_serve_async(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> PoolServePersistResult:
        """Commit one serve batch on the interactive SQLite worker."""
        loop = asyncio.get_running_loop()
        executor = self._executor(maintenance=False)
        return await loop.run_in_executor(
            executor,
            self.persist_pool_serve,
            items,
            shown_bvids,
        )

    async def count_pool_readiness_isolated_async(
        self,
        *,
        xhs_self_nickname: str = "",
    ) -> dict[str, int]:
        """Read exact inventory without touching the process-shared connection."""
        loop = asyncio.get_running_loop()
        executor = self._executor(maintenance=False)
        return await loop.run_in_executor(
            executor,
            partial(
                self.count_pool_readiness_isolated,
                xhs_self_nickname=xhs_self_nickname,
            ),
        )

    def _isolated_database(self, *, busy_timeout_ms: int) -> Database:
        """Return a lightweight wrapper bound to a short-lived connection.

        The wrapper reuses all connection-aware SQL already implemented by
        ``Database`` without swapping ``self._conn`` (which would race the
        process-shared connection). It owns no worker threads of its own.
        """
        conn = self.open_connection()
        conn.execute(f"PRAGMA busy_timeout = {max(0, int(busy_timeout_ms))}")
        isolated = Database(self._db_path)
        isolated._conn = conn
        isolated._admission_min_score = self._admission_min_score
        isolated._publication_date_preference = self._publication_date_preference
        isolated._source_publication_date_preferences = self._source_publication_date_preferences
        isolated._seen_state_lock = self._seen_state_lock
        isolated._seen_state_cache = self._seen_state_cache
        return isolated

    def load_pool_serve_snapshot(
        self,
        *,
        limit: int,
        xhs_self_nickname: str = "",
        curator_history_limit: int = 30,
        source_platform: str = "",
    ) -> PoolServeSnapshot:
        """Load all hot-path pool state through one isolated read snapshot.

        ``source_platform`` narrows the candidate rows to a single canonical
        family for platform-scoped requests. Only the candidate set changes:
        ``readiness`` stays pool-wide (it is the inventory the API reports and
        replenishes against), and every downstream ranking, persistence and
        shown-commit step is shared with the cross-platform path. Strict mode
        also skips the platform floor — topping a scoped batch up with another
        platform is exactly the leak the scope exists to prevent.
        """
        scope = normalize_source_platform(source_platform)
        isolated = self._isolated_database(busy_timeout_ms=_INTERACTIVE_DB_BUSY_TIMEOUT_MS)
        try:
            isolated.retire_temporally_stale_pool_items()
        except sqlite3.OperationalError:
            # Canonical availability applies the same pure policy in memory,
            # and the final serve transaction rechecks it again.  A transient
            # maintenance lock therefore must not turn recommendation reads
            # into a 500 response.
            logger.warning(
                "temporal pool retirement deferred by SQLite contention; "
                "serving with in-memory eligibility guard",
            )
        isolated._preserve_read_transaction = True
        try:
            isolated.conn.execute("BEGIN")
            # Materialize the canonical all-time seen ledger once and reuse it
            # across every availability/candidate helper in this transaction.
            viewed_content_keys, seen_bvids = isolated._seen_state_on(isolated.conn)
            readiness = isolated.count_pool_readiness(
                xhs_self_nickname=xhs_self_nickname,
                _viewed_content_keys=viewed_content_keys,
            )
            if scope:
                # Take the platform's whole available set, then apply the same
                # topic round-robin ``get_pool_candidates`` uses. Truncating by
                # relevance instead would fill the window with a few dominant
                # topic_groups, leaving the downstream MMR/diversity stages a
                # far narrower window than the mixed feed gets — a platform tab
                # would read as visibly more repetitive than "全部" for no
                # reason other than how its candidates were loaded.
                rows = Database._balance_pool_rows(
                    isolated._available_platform_rows_on(
                        isolated.conn,
                        scope,
                        xhs_self_nickname=xhs_self_nickname,
                        _viewed_content_keys=viewed_content_keys,
                    ),
                    limit=max(0, int(limit)),
                )
            else:
                rows = isolated.get_pool_candidates(
                    limit=max(0, int(limit)),
                    xhs_self_nickname=xhs_self_nickname,
                    _viewed_content_keys=viewed_content_keys,
                )
            loaded_count = len(rows)

            topups: list[tuple[str, int]] = []
            if not scope:
                present = {
                    str(row.get("source_platform", "") or "").strip().lower() or "bilibili"
                    for row in rows
                }
                platforms = isolated.list_servable_pool_platforms(
                    xhs_self_nickname=xhs_self_nickname,
                    _viewed_content_keys=viewed_content_keys,
                )
                missing = [
                    token
                    for token in (str(name).strip().lower() for name in platforms)
                    if token and token not in present
                ]
                seen = {str(row.get("bvid", "")) for row in rows if row.get("bvid")}
                if len(platforms) > 1 and missing:
                    # Materialize the canonical available set once and share it
                    # across every top-up. Reading it per platform turned an
                    # all-bilibili window with seven stocked platforms into
                    # seven full-row scans (~35ms each) on the serve hot path;
                    # the cheap platform list above keeps that scan off the
                    # common case where no platform is missing at all.
                    available_rows = isolated._load_available_pool_candidate_rows_on(
                        isolated.conn,
                        xhs_self_nickname=xhs_self_nickname,
                        _viewed_content_keys=viewed_content_keys,
                        full_rows=True,
                    )
                    for token in missing:
                        added = 0
                        for row in isolated._available_platform_rows_on(
                            isolated.conn,
                            token,
                            limit=5,
                            xhs_self_nickname=xhs_self_nickname,
                            _viewed_content_keys=viewed_content_keys,
                            _available_rows=available_rows,
                        ):
                            bvid = str(row.get("bvid", ""))
                            if bvid and bvid in seen:
                                continue
                            if bvid:
                                seen.add(bvid)
                            rows.append(row)
                            added += 1
                        if added:
                            topups.append((token, added))

            viewed = frozenset(seen_bvids)
            history_limit = max(1, int(curator_history_limit))
            curator_signals = isolated.get_recent_recommendation_signals(limit=history_limit)
            feedback_signals = isolated.get_feedback_signals(limit=history_limit)
            isolated.conn.commit()
            return PoolServeSnapshot(
                readiness={key: max(0, int(value)) for key, value in readiness.items()},
                candidate_rows=tuple(rows),
                loaded_count=loaded_count,
                platform_topups=tuple(topups),
                seen_bvids=viewed,
                curator_signals=tuple(curator_signals),
                feedback_signals=tuple(feedback_signals),
            )
        except Exception:
            isolated.conn.rollback()
            raise
        finally:
            isolated._preserve_read_transaction = False
            isolated.close()

    def persist_pool_serve(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> PoolServePersistResult:
        """Persist recommendation rows and shown state on an isolated connection."""
        isolated = self._isolated_database(busy_timeout_ms=_INTERACTIVE_DB_BUSY_TIMEOUT_MS)
        try:
            return isolated.batch_insert_eligible_recommendations_and_mark_shown(
                items,
                shown_bvids,
            )
        finally:
            isolated.close()

    def count_pool_readiness_isolated(
        self,
        *,
        xhs_self_nickname: str = "",
    ) -> dict[str, int]:
        """Read exact inventory using a short-lived connection."""
        isolated = self._isolated_database(busy_timeout_ms=_INTERACTIVE_DB_BUSY_TIMEOUT_MS)
        try:
            return isolated.count_pool_readiness(xhs_self_nickname=xhs_self_nickname)
        finally:
            isolated.close()

    def load_pool_platform_availability(
        self,
        *,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
    ) -> PoolPlatformAvailability:
        """Read total and per-platform servable inventory in one transaction.

        Both numbers derive from a single materialization of the canonical
        available set, so ``total_available == sum(by_platform.values())`` is
        structural rather than a coincidence of two independent queries that
        could observe different WAL states. Runs on a short-lived connection
        so a tab-count refresh never contends with the process-shared one.
        """
        isolated = self._isolated_database(busy_timeout_ms=_INTERACTIVE_DB_BUSY_TIMEOUT_MS)
        isolated._preserve_read_transaction = True
        try:
            isolated.conn.execute("BEGIN")
            viewed_content_keys, _ = isolated._seen_state_on(isolated.conn)
            rows = isolated._load_available_pool_candidate_rows_on(
                isolated.conn,
                max_per_topic_group=max_per_topic_group,
                xhs_self_nickname=xhs_self_nickname,
                _viewed_content_keys=viewed_content_keys,
            )
            isolated.conn.commit()
        except Exception:
            isolated.conn.rollback()
            raise
        finally:
            isolated._preserve_read_transaction = False
            isolated.close()
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            counts[_pool_source_family(row["source"], row["source_platform"])] += 1
        return PoolPlatformAvailability(
            total_available=len(rows),
            by_platform=dict(counts),
        )

    async def load_pool_platform_availability_async(
        self,
        *,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
    ) -> PoolPlatformAvailability:
        """Read platform inventory on the interactive SQLite worker."""
        loop = asyncio.get_running_loop()
        executor = self._executor(maintenance=False)
        return await loop.run_in_executor(
            executor,
            partial(
                self.load_pool_platform_availability,
                max_per_topic_group=max_per_topic_group,
                xhs_self_nickname=xhs_self_nickname,
            ),
        )

    def _ensure_fresh_read(self) -> None:
        """Close any implicit transaction so the next SELECT sees the latest WAL state.

        When a CLI command (a separate process) writes to the same database,
        this server process may still hold a stale read snapshot inside an
        implicit transaction.  Committing closes that transaction so the next
        query starts a new one against the current WAL head.
        """
        if self.conn.in_transaction and not self._preserve_read_transaction:
            self.conn.commit()

    def _execute_write(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Cursor:
        """Execute a write with short retry on transient SQLite locks."""
        connection = self.conn
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = connection.execute(sql, params)
                connection.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                self._rollback_failed_write(connection)
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def _execute_prepared_write(
        self,
        sql: str,
        prepare_params: Callable[[sqlite3.Connection], _CacheContentPreparedWrite],
    ) -> CacheContentWriteResult:
        """Run a cache upsert and capture its result under one writer lock."""

        connection = self.conn
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                if connection.in_transaction:
                    # Some legacy callers stage writes directly on ``conn``
                    # and then use ``cache_content`` as the committing sink.
                    # A no-op DML upgrades that existing deferred transaction
                    # to a writer transaction without attempting a nested
                    # BEGIN; the time-sensitive params are still derived only
                    # after SQLite grants the writer lock.
                    connection.execute("UPDATE content_cache SET bvid = bvid WHERE 0")
                else:
                    connection.execute("BEGIN IMMEDIATE")
                try:
                    prepared = prepare_params(connection)
                    connection.execute(sql, prepared.params)
                    if prepared.renewed_review_retry_at:
                        connection.execute(
                            """
                            UPDATE content_cache
                            SET temporal_review_attempts = MAX(
                                    COALESCE(temporal_review_attempts, 0),
                                    1
                                ),
                                temporal_review_retry_at = ?
                            WHERE bvid = ?
                              AND pool_status = 'temporal_review_hold'
                            """,
                            (prepared.renewed_review_retry_at, prepared.bvid),
                        )
                    row = connection.execute(
                        "SELECT COALESCE(pool_status, 'fresh') AS pool_status "
                        "FROM content_cache WHERE bvid = ? LIMIT 1",
                        (prepared.bvid,),
                    ).fetchone()
                    if row is None:  # pragma: no cover - upsert invariant
                        raise RuntimeError("content_cache upsert did not persist its target row")
                    result = CacheContentWriteResult(
                        bvid=prepared.bvid,
                        created=prepared.created,
                        pool_status=str(row["pool_status"] or "fresh"),
                        temporal_decision=prepared.temporal_decision,
                    )
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                self._rollback_failed_write(connection)
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite prepared write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def _execute_many_write(
        self,
        sql: str,
        seq_of_params: Sequence[tuple[Any, ...] | list[Any]],
    ) -> sqlite3.Cursor:
        """Batch-execute a write with the same transient-lock retry as ``_execute_write``."""
        connection = self.conn
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = connection.executemany(sql, seq_of_params)
                connection.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                self._rollback_failed_write(connection)
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite batch write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    @staticmethod
    def _rollback_failed_write(connection: sqlite3.Connection) -> None:
        """Reset an implicit transaction before retrying or surfacing failure.

        A lock error may leave Python's connection inside an implicit
        transaction. Retrying on that state can mix the next statement with
        the failed one (and used to amplify cross-thread connection misuse).
        Test doubles predating this boundary may not expose ``rollback``.
        """
        rollback = getattr(connection, "rollback", None)
        if callable(rollback):
            rollback()

    def insert_event(self, event_type: str, **kwargs: Any) -> int:
        """Insert a behavioral event.

        v0.3.23+: ``context`` is now a natural-language string (from
        ``event_format.build_event()``). It's stored as raw text — no
        outer JSON wrapping — so consumers reading via SELECT get back
        the same string they put in. Pre-v0.3.22 callers that passed
        dict-shaped context still work: dicts / lists / other non-string
        values are JSON-encoded for storage so older code paths don't
        suddenly lose data.

        Args:
            event_type: Type of event.
            **kwargs: Additional event fields. ``context`` may be str,
                dict, list, or None.

        Returns:
            Inserted row ID.
        """
        [result] = self.insert_events_with_receipts([{"event_type": event_type, **kwargs}])
        return result.event_id

    @staticmethod
    def _event_insert_params(event_type: str, kwargs: Mapping[str, Any]) -> tuple[Any, ...]:
        """Normalize one event into the canonical SQLite row shape."""
        import json

        from openbiliclaw.sources.event_format import classify_event_satisfaction

        raw_context = kwargs.get("context", "")
        if isinstance(raw_context, str):
            context_text = raw_context
        elif raw_context is None:
            context_text = ""
        else:
            context_text = json.dumps(raw_context, ensure_ascii=False)
        raw_metadata = kwargs.get("metadata", {})
        metadata_payload = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        explicit_platform = kwargs.get("source_platform", "")
        metadata_platform = metadata_payload.get("source_platform", "")
        provided_confidence = str(kwargs.get("source_confidence", "") or "").strip()
        source_platform, detected_confidence = resolve_source_attribution(
            explicit_platform=explicit_platform,
            metadata_platform=metadata_platform,
            url=kwargs.get("url", ""),
        )
        # Callers may suggest a confidence value, but they cannot claim
        # stronger evidence than the resolver observed. This keeps a direct
        # ``insert_event(..., url=x.com, source_confidence="exact")`` from
        # turning URL inference into exact attribution.
        source_confidence = constrain_source_confidence(
            provided_confidence,
            detected_confidence,
        )
        # A confidence value without a resolved platform cannot be evidence
        # for a source. Keep the row explicitly unknown at the storage
        # boundary even when a legacy caller supplied "exact".
        if not source_platform:
            source_confidence = SOURCE_CONFIDENCE_LEGACY_UNKNOWN
        if not source_platform:
            source_platform = normalize_source_platform(explicit_platform or metadata_platform)
        if source_platform:
            metadata_payload["source_platform"] = source_platform
        content_id = str(kwargs.get("content_id", "") or "").strip() or extract_source_content_id(
            metadata_payload
        )
        if content_id:
            metadata_payload["content_id"] = content_id
        classifier_event: dict[str, Any] = {
            "event_type": event_type,
            "url": kwargs.get("url", ""),
            "title": kwargs.get("title", ""),
            "metadata": metadata_payload if isinstance(metadata_payload, dict) else {},
        }
        for top_level_key in ("watch_seconds", "video_duration_seconds"):
            if top_level_key in kwargs and kwargs[top_level_key] is not None:
                classifier_event[top_level_key] = kwargs[top_level_key]
        inferred_satisfaction, satisfaction_reason = classify_event_satisfaction(classifier_event)
        ingest_key = str(kwargs.get("ingest_key", "") or "").strip()
        if len(ingest_key) > 512:
            raise ValueError("ingest_key exceeds 512 characters")
        return (
            event_type,
            kwargs.get("url", ""),
            kwargs.get("title", ""),
            source_platform,
            content_id,
            source_confidence,
            context_text,
            json.dumps(metadata_payload, ensure_ascii=False),
            inferred_satisfaction,
            satisfaction_reason,
            ingest_key,
        )

    def insert_events_with_receipts(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> list[EventInsertResult]:
        """Atomically insert valid events and return stable duplicate receipts.

        A non-empty ``ingest_key`` is a producer-owned identity. Replays return
        the original row id without creating another event. Empty keys preserve
        legacy append-only behaviour. All input rows share one transaction.
        """
        rows = [
            self._event_insert_params(
                str(event.get("event_type") or event.get("type") or "").strip(),
                event,
            )
            for event in events
        ]
        if not rows:
            return []
        attempts = _LOCK_RETRY_ATTEMPTS
        conn = self.open_connection()
        try:
            while True:
                try:
                    results: list[EventInsertResult] = []
                    seen_changed = False
                    newest_inserted_id = 0
                    for params in rows:
                        ingest_key = str(params[10])
                        if ingest_key:
                            cursor = conn.execute(
                                """
                                INSERT INTO events (
                                    event_type, url, title, source_platform, content_id,
                                    source_confidence, context, metadata,
                                    inferred_satisfaction, satisfaction_reason, ingest_key
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(ingest_key) WHERE ingest_key <> '' DO NOTHING
                                """,
                                params,
                            )
                        else:
                            cursor = conn.execute(
                                """
                                INSERT INTO events (
                                    event_type, url, title, source_platform, content_id,
                                    source_confidence, context, metadata,
                                    inferred_satisfaction, satisfaction_reason, ingest_key
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                params,
                            )
                        inserted = int(cursor.rowcount or 0) == 1
                        if inserted:
                            event_id = int(cursor.lastrowid or 0)
                            stored_event_type = str(params[0])
                            newest_inserted_id = max(newest_inserted_id, event_id)
                            if stored_event_type in _SEEN_ITEM_EVENT_TYPES and event_id > 0:
                                seen_changed = (
                                    self._upsert_seen_items_from_view_event_on(
                                        conn,
                                        event_id=event_id,
                                        row={"url": params[1], "metadata": params[7]},
                                    )
                                    or seen_changed
                                )
                        else:
                            existing = conn.execute(
                                "SELECT id, event_type FROM events WHERE ingest_key = ?",
                                (ingest_key,),
                            ).fetchone()
                            if existing is None:  # pragma: no cover - guarded by unique index
                                raise RuntimeError("duplicate event row disappeared")
                            event_id = int(existing["id"])
                            stored_event_type = str(existing["event_type"])
                        results.append(
                            EventInsertResult(
                                event_id=event_id,
                                event_type=stored_event_type,
                                ingest_key=ingest_key,
                                inserted=inserted,
                            )
                        )
                    self._advance_seen_items_cursor_on(conn, newest_inserted_id)
                    conn.commit()
                    if seen_changed:
                        self._invalidate_seen_state_cache()
                    return results
                except sqlite3.OperationalError as exc:
                    conn.rollback()
                    message = str(exc).lower()
                    if "database is locked" not in message or attempts <= 1:
                        raise
                    attempts -= 1
                    logger.warning(
                        "SQLite event+seen write locked, retrying (%s attempts left)",
                        attempts,
                    )
                    time.sleep(_LOCK_RETRY_SLEEP_SECONDS)
                except Exception:
                    conn.rollback()
                    raise
        finally:
            conn.close()

    def insert_events_batch(self, events: Sequence[Mapping[str, Any]]) -> int:
        """Insert many normalized events in one isolated transaction.

        Guided init can import hundreds of rows. Using an isolated connection
        keeps SQLite's busy wait off the API event loop and one transaction
        avoids hundreds of commit windows competing with background writers.
        """
        results = self.insert_events_with_receipts(events)
        return sum(1 for result in results if result.inserted)

    def mark_positive_events_retracted(
        self,
        identity_urls: list[str],
        retracted_action: str,
        *,
        retraction_at: datetime,
    ) -> int:
        """Discount stored positive events undone by a retraction (Phase 0 face 2).

        Covers offline reread paths (``openbiliclaw init`` full rebuild, the 12h
        cognition/profile-consolidation cycle) that re-read the events table. For
        every ``retracted_action`` row whose URL normalizes to the same identity
        key as ``identity_urls`` **and whose event time is strictly earlier than
        ``retraction_at``**, mark ``metadata.retracted=true`` and cap
        ``signal_strength`` at 0.2. Rows without a usable event time are
        conservatively skipped (same causality rule as the in-memory face).

        Identity keys (tweet_id / bvid / mid / xhs note_id) are globally unique,
        so there is no time-window guard — undoing a months-old like is exactly
        the case this must catch. Rows are never deleted and non-metadata
        columns are never rewritten (invariant 3).

        Returns the number of rows marked.
        """
        from datetime import UTC

        from openbiliclaw.sources.event_format import (
            RETRACTABLE_ACTIONS,
            apply_retraction_discount,
            parse_event_timestamp,
        )
        from openbiliclaw.sources.identity_keys import dedup_key

        action = str(retracted_action or "").strip().lower()
        if action not in RETRACTABLE_ACTIONS:
            return 0
        target_keys = {key for url in identity_urls if (key := dedup_key(str(url or "")))}
        if not target_keys:
            return 0

        cutoff = retraction_at if retraction_at.tzinfo else retraction_at.replace(tzinfo=UTC)

        conn = self.open_connection()
        try:
            rows = conn.execute(
                "SELECT id, url, metadata FROM events WHERE event_type = ?", (action,)
            ).fetchall()
            marked = 0
            for row in rows:
                if dedup_key(str(row["url"] or "")) not in target_keys:
                    continue
                try:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(metadata, dict):
                    continue
                event_time = parse_event_timestamp(metadata)
                if event_time is None or event_time >= cutoff:
                    continue
                patched = apply_retraction_discount(metadata)
                conn.execute(
                    "UPDATE events SET metadata = ? WHERE id = ?",
                    (json.dumps(patched, ensure_ascii=False), row["id"]),
                )
                marked += 1
            conn.commit()
            return marked
        finally:
            conn.close()

    def discount_events_by_confusion(self, evidence_refs: Sequence[str]) -> int:
        """Discount events a proxy-resolved confusion pointed at (Phase 2 exit).

        When a confusion resolves as ``proxy_behavior`` / misread, the behaviours
        it was raised over should stop driving preference weight. For every
        ``evidence_ref`` that parses to a stored event id, mark
        ``metadata.discounted_by_confusion=true`` and cap ``signal_strength``
        (same ``apply_confusion_discount`` patch the offline rereads honour).
        Non-integer refs (topics / note ids) are skipped. Rows are never
        deleted and non-metadata columns are never rewritten. Returns the count
        of rows patched.
        """
        from openbiliclaw.sources.event_format import apply_confusion_discount

        event_ids: list[int] = []
        for ref in evidence_refs:
            try:
                event_ids.append(int(str(ref).strip()))
            except (TypeError, ValueError):
                continue
        if not event_ids:
            return 0
        conn = self.open_connection()
        try:
            placeholders = ", ".join("?" for _ in event_ids)
            rows = conn.execute(
                f"SELECT id, metadata FROM events WHERE id IN ({placeholders})",
                event_ids,
            ).fetchall()
            marked = 0
            for row in rows:
                try:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(metadata, dict):
                    continue
                patched = apply_confusion_discount(metadata)
                conn.execute(
                    "UPDATE events SET metadata = ? WHERE id = ?",
                    (json.dumps(patched, ensure_ascii=False), row["id"]),
                )
                marked += 1
            conn.commit()
            return marked
        finally:
            conn.close()

    def latest_retraction_time_for(self, url: str, action: str) -> datetime | None:
        """Return the newest stored retraction time for this identity + action.

        Powers late-arriving positive reconciliation (Phase 0 face 2b): a
        positive persisted after its retraction is already in the events table
        (e.g. account_sync backfilling a months-old like) must still be marked.
        The events table is the durable tombstone. Returns ``None`` when no
        matching retraction exists or none carry a usable event time.
        """
        from openbiliclaw.sources.event_format import RETRACTABLE_ACTIONS, parse_event_timestamp
        from openbiliclaw.sources.identity_keys import dedup_key

        normalized_action = str(action or "").strip().lower()
        if normalized_action not in RETRACTABLE_ACTIONS:
            return None
        target_key = dedup_key(str(url or ""))
        if not target_key:
            return None

        rows = self.conn.execute(
            "SELECT url, metadata FROM events WHERE event_type = 'feedback'"
        ).fetchall()
        latest: datetime | None = None
        for row in rows:
            try:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("feedback_type") or "").strip().lower() != "retraction":
                continue
            if str(metadata.get("retracted_action") or "").strip().lower() != normalized_action:
                continue
            if dedup_key(str(row["url"] or "")) != target_key:
                continue
            event_time = parse_event_timestamp(metadata)
            if event_time is not None and (latest is None or event_time > latest):
                latest = event_time
        return latest

    def load_retraction_index(self) -> dict[tuple[str, str], datetime]:
        """Load newest durable retraction times in one isolated table scan."""
        from openbiliclaw.sources.event_format import RETRACTABLE_ACTIONS, parse_event_timestamp
        from openbiliclaw.sources.identity_keys import dedup_key

        conn = self.open_connection()
        try:
            rows = conn.execute(
                "SELECT url, metadata FROM events WHERE event_type = 'feedback'"
            ).fetchall()
        finally:
            conn.close()
        index: dict[tuple[str, str], datetime] = {}
        for row in rows:
            try:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("feedback_type") or "").strip().lower() != "retraction":
                continue
            action = str(metadata.get("retracted_action") or "").strip().lower()
            identity_key = dedup_key(str(row["url"] or ""))
            if action not in RETRACTABLE_ACTIONS or not identity_key:
                continue
            event_time = parse_event_timestamp(metadata)
            marker = (identity_key, action)
            if event_time is not None and (marker not in index or event_time > index[marker]):
                index[marker] = event_time
        return index

    def get_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent events.

        Args:
            limit: Maximum number of events.

        Returns:
            List of event dicts.
        """
        cursor = self.conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Durable popup chat turns
    # ------------------------------------------------------------------

    def create_chat_turn(
        self,
        *,
        turn_id: str,
        message: str,
        session: str = "popup",
        scope: str = "chat",
        subject_id: str = "",
        subject_title: str = "",
        reply_to_turn_id: str = "",
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Create a pending popup chat turn if it does not already exist."""
        serialized_payload = json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True)
        self._execute_write(
            """
            INSERT OR IGNORE INTO chat_turns (
                turn_id, session, scope, subject_id, subject_title, reply_to_turn_id,
                message, status, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                turn_id,
                session or "popup",
                scope or "chat",
                subject_id or "",
                subject_title or "",
                reply_to_turn_id or "",
                message,
                serialized_payload,
            ),
        )
        row = self.get_chat_turn(turn_id)
        if row is None:
            raise RuntimeError(f"Failed to create chat turn {turn_id!r}")
        return row

    def get_chat_confirmation_turn(
        self,
        *,
        ref: str,
        session: str,
    ) -> dict[str, Any] | None:
        """Return the active confirmation entry for one ``(ref, session)``."""
        conn = self.open_connection()
        try:
            row = conn.execute(
                """
                SELECT turn_id, session, scope, subject_id, subject_title,
                       reply_to_turn_id, message,
                       status, reply, error, payload, created_at, updated_at
                FROM chat_turns
                WHERE session = ?
                  AND subject_id = ?
                  AND status = 'completed'
                  AND json_valid(payload)
                  AND json_extract(payload, '$.ref') = ?
                  AND (
                        (
                            scope = 'hypothesis'
                            AND json_extract(payload, '$.type') = 'card'
                            AND json_extract(payload, '$.state') IN ('pending', 'discussing')
                        )
                        OR (
                            scope = 'confusion'
                            AND json_extract(payload, '$.type') = 'question'
                        )
                      )
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (session or "popup", ref, ref),
            ).fetchone()
            return self._normalize_chat_turn_row(row) if row is not None else None
        finally:
            conn.close()

    def get_chat_confirmation_attachment(
        self,
        *,
        attached_to_turn_id: str,
        session: str,
    ) -> dict[str, Any] | None:
        """Return a card/question already attached to one durable user turn."""
        conn = self.open_connection()
        try:
            row = conn.execute(
                """
                SELECT turn_id, session, scope, subject_id, subject_title,
                       reply_to_turn_id, message,
                       status, reply, error, payload, created_at, updated_at
                FROM chat_turns
                WHERE session = ?
                  AND status = 'completed'
                  AND json_valid(payload)
                  AND json_extract(payload, '$.attached_to_turn_id') = ?
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """,
                (session or "popup", attached_to_turn_id),
            ).fetchone()
            return self._normalize_chat_turn_row(row) if row is not None else None
        finally:
            conn.close()

    def create_chat_confirmation_turn(
        self,
        *,
        turn_id: str,
        session: str,
        scope: str,
        ref: str,
        title: str,
        message: str,
        reply: str,
        payload: Mapping[str, object],
    ) -> tuple[dict[str, Any], bool]:
        """Atomically deduplicate ``(ref, session)`` and insert a completed entry.

        ``BEGIN IMMEDIATE`` serializes the lookup and INSERT across API workers.
        The optional ``attached_to_turn_id`` payload key is checked first so a
        crash after the card INSERT but before its user turn can resume without
        selecting or attaching a second object.
        """
        normalized_scope = scope.strip()
        if normalized_scope not in {"hypothesis", "confusion"}:
            raise ValueError(f"Unsupported confirmation scope: {scope!r}")
        normalized_session = session.strip() or "popup"
        normalized_ref = ref.strip()
        if not normalized_ref:
            raise ValueError("Confirmation ref is required")
        serialized_payload = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        attached_to_turn_id = str(payload.get("attached_to_turn_id", "")).strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row: sqlite3.Row | None = None
            if attached_to_turn_id:
                row = conn.execute(
                    """
                    SELECT turn_id, session, scope, subject_id, subject_title,
                           reply_to_turn_id, message,
                           status, reply, error, payload, created_at, updated_at
                    FROM chat_turns
                    WHERE session = ?
                      AND status = 'completed'
                      AND json_valid(payload)
                      AND json_extract(payload, '$.attached_to_turn_id') = ?
                    ORDER BY created_at ASC, rowid ASC
                    LIMIT 1
                    """,
                    (normalized_session, attached_to_turn_id),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT turn_id, session, scope, subject_id, subject_title,
                           reply_to_turn_id, message,
                           status, reply, error, payload, created_at, updated_at
                    FROM chat_turns
                    WHERE session = ?
                      AND subject_id = ?
                      AND status = 'completed'
                      AND json_valid(payload)
                      AND json_extract(payload, '$.ref') = ?
                      AND (
                            (
                                scope = 'hypothesis'
                                AND json_extract(payload, '$.type') = 'card'
                                AND json_extract(payload, '$.state')
                                    IN ('pending', 'discussing')
                            )
                            OR (
                                scope = 'confusion'
                                AND json_extract(payload, '$.type') = 'question'
                            )
                          )
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (normalized_session, normalized_ref, normalized_ref),
                ).fetchone()
            if row is not None:
                conn.commit()
                return self._normalize_chat_turn_row(row), False
            conn.execute(
                """
                INSERT INTO chat_turns (
                    turn_id, session, scope, subject_id, subject_title, reply_to_turn_id,
                    message, status, reply, error, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?, '', ?)
                """,
                (
                    turn_id,
                    normalized_session,
                    normalized_scope,
                    normalized_ref,
                    title.strip(),
                    str(payload.get("reply_to_turn_id", "") or "").strip(),
                    message,
                    reply,
                    serialized_payload,
                ),
            )
            row = conn.execute(
                """
                SELECT turn_id, session, scope, subject_id, subject_title,
                       reply_to_turn_id, message,
                       status, reply, error, payload, created_at, updated_at
                FROM chat_turns
                WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
            if row is None:  # pragma: no cover - guarded by the INSERT above
                raise RuntimeError(f"Failed to create confirmation turn {turn_id!r}")
            conn.commit()
            return self._normalize_chat_turn_row(row), True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_chat_turn(self, turn_id: str, *, reply: str) -> bool:
        """CAS a pending popup chat turn to completed exactly once."""
        cursor = self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'completed',
                reply = ?,
                error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
              AND status = 'pending'
            """,
            (reply, turn_id),
        )
        return cursor.rowcount == 1

    def fail_chat_turn(self, turn_id: str, *, error: str, reply: str = "") -> bool:
        """CAS a pending popup chat turn to an explicit terminal failure."""
        cursor = self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'failed',
                reply = ?,
                error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
              AND status = 'pending'
            """,
            (reply, error, turn_id),
        )
        return cursor.rowcount == 1

    def get_chat_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Return one durable popup chat turn by id."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT turn_id, session, scope, subject_id, subject_title,
                   reply_to_turn_id, message,
                   status, reply, error, payload, created_at, updated_at
            FROM chat_turns
            WHERE turn_id = ?
            """,
            (turn_id,),
        )
        row = cursor.fetchone()
        return self._normalize_chat_turn_row(row) if row else None

    def list_chat_turns(
        self,
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent popup chat turns in display order."""
        self._ensure_fresh_read()
        clauses = ["session = ?"]
        params: list[Any] = [session or "popup"]
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        params.append(max(1, int(limit)))
        cursor = self.conn.execute(
            f"""
            SELECT turn_id, session, scope, subject_id, subject_title, reply_to_turn_id, message,
                   status, reply, error, payload, created_at, updated_at
            FROM (
                   SELECT rowid AS insertion_rowid,
                       turn_id, session, scope, subject_id, subject_title,
                       reply_to_turn_id, message,
                       status, reply, error, payload, created_at, updated_at
                FROM chat_turns
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, insertion_rowid ASC
            """,
            params,
        )
        return [self._normalize_chat_turn_row(row) for row in cursor.fetchall()]

    def list_pending_chat_turn_ids(self, *, limit: int | None = None) -> list[str]:
        """Return recoverable pending turns in durable insertion order."""
        self._ensure_fresh_read()
        query = """
            SELECT turn_id
            FROM chat_turns
            WHERE status = 'pending'
            ORDER BY created_at ASC, rowid ASC
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (max(1, int(limit)),)
        cursor = self.conn.execute(query, params)
        return [str(row["turn_id"]) for row in cursor.fetchall()]

    def list_pending_chat_turn_page(
        self,
        *,
        after_rowid: int = 0,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        """Page every recoverable pending turn by immutable insertion rowid."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT rowid, turn_id
            FROM chat_turns
            WHERE status = 'pending'
              AND rowid > ?
            ORDER BY rowid ASC
            LIMIT ?
            """,
            (max(0, int(after_rowid)), max(1, int(limit))),
        )
        return [
            {"rowid": int(row["rowid"]), "turn_id": str(row["turn_id"])}
            for row in cursor.fetchall()
        ]

    def count_pending_chat_turns(self) -> int:
        """Return the authoritative durable chat reply backlog depth."""
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT COUNT(*) AS count FROM chat_turns WHERE status = 'pending'"
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    @staticmethod
    def _normalize_chat_turn_row(row: sqlite3.Row) -> dict[str, Any]:
        """Decode the additive chat payload while tolerating legacy/corrupt rows."""
        normalized = dict(row)
        raw_payload = normalized.get("payload", "{}")
        try:
            parsed = json.loads(str(raw_payload or "{}"))
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = {}
        normalized["payload"] = parsed if isinstance(parsed, dict) else {}
        # Additive relation migration: legacy rows have no relation and must
        # hydrate as explicitly unbound rather than crashing or guessing.
        normalized.setdefault("reply_to_turn_id", "")
        return normalized

    def update_chat_turn_payload_state(
        self,
        turn_id: str,
        *,
        expected_state: str,
        new_state: str,
    ) -> bool:
        """Atomically move a card payload through its declared state graph."""
        allowed_transitions = {
            "pending": frozenset({"confirmed", "rejected", "revised", "deferred", "discussing"}),
            "discussing": frozenset({"confirmed", "rejected", "revised", "deferred", "pending"}),
        }
        if new_state not in allowed_transitions.get(expected_state, frozenset()):
            raise ValueError(
                f"Unsupported card payload transition: {expected_state!r} -> {new_state!r}"
            )
        cursor = self._execute_write(
            """
            UPDATE chat_turns
            SET payload = json_set(payload, '$.state', ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
              AND json_valid(payload)
              AND json_extract(payload, '$.state') = ?
            """,
            (new_state, turn_id, expected_state),
        )
        return int(cursor.rowcount or 0) == 1

    def store_chat_turn_effect_receipt(
        self,
        turn_id: str,
        *,
        receipt_key: str,
        receipt: Mapping[str, object],
    ) -> bool:
        """Persist one dialogue side-effect checkpoint inside the turn payload."""
        if receipt_key not in {"probe_reply_apply", "confusion_reply_apply"}:
            raise ValueError(f"Unsupported chat-turn effect receipt: {receipt_key!r}")
        serialized = json.dumps(dict(receipt), ensure_ascii=False, sort_keys=True)
        cursor = self._execute_write(
            """
            UPDATE chat_turns
            SET payload = json_set(
                    CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,
                    ?,
                    json(?)
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
            """,
            (f"$.{receipt_key}", serialized, turn_id),
        )
        return int(cursor.rowcount or 0) == 1

    def try_create_card_settlement(
        self,
        *,
        ref: str,
        verdict: str,
        turn_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        """Atomically reserve one object verdict with SQLite INSERT OR IGNORE."""
        normalized_ref = _validated_card_settlement_ref(ref)
        cursor = self._execute_write(
            """
            INSERT OR IGNORE INTO card_settlements (ref, verdict, turn_id, payload, applied)
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                normalized_ref,
                verdict,
                turn_id,
                json.dumps(dict(payload or {}), ensure_ascii=False, sort_keys=True),
            ),
        )
        return int(cursor.rowcount or 0) == 1

    def get_card_settlement(self, ref: str) -> dict[str, Any] | None:
        """Return the durable settlement arbitration row for one object."""
        normalized_ref = _validated_card_settlement_ref(ref)
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT ref, verdict, turn_id, payload, applied, result, event_id,
                   created_at, updated_at
            FROM card_settlements
            WHERE ref = ?
            """,
            (normalized_ref,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("payload", "result"):
            raw_payload = result.get(field)
            try:
                decoded = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            except (TypeError, ValueError):
                decoded = {}
            result[field] = decoded if isinstance(decoded, dict) else {}
        return result

    def record_card_settlement_event_once(
        self,
        *,
        ref: str,
        event: Mapping[str, Any],
    ) -> bool:
        """Atomically mark and insert the receipt's stable feedback event."""
        normalized_ref = _validated_card_settlement_ref(ref)
        event_id = _card_settlement_effect_key(normalized_ref, "event")
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        if not event_type:
            raise ValueError("Settlement event type is required")
        params = self._event_insert_params(event_type, event)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT event_id
                FROM card_settlements
                WHERE ref = ?
                """,
                (normalized_ref,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Dialogue settlement receipt disappeared")
            if str(row["event_id"] or ""):
                conn.commit()
                return False
            conn.execute(
                """
                INSERT INTO events (
                    event_type, url, title, source_platform, content_id,
                    source_confidence, context, metadata,
                    inferred_satisfaction, satisfaction_reason, ingest_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            updated = conn.execute(
                """
                UPDATE card_settlements
                SET event_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE ref = ? AND event_id = ''
                """,
                (event_id, normalized_ref),
            )
            if int(updated.rowcount or 0) != 1:  # pragma: no cover - BEGIN IMMEDIATE owns writer
                raise RuntimeError("Dialogue settlement event marker was not recorded")
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_card_settlement(
        self,
        *,
        ref: str,
        result: Mapping[str, Any] | None = None,
    ) -> bool:
        """Mark a receipt applied after mandatory worker effects complete."""
        normalized_ref = _validated_card_settlement_ref(ref)
        cursor = self._execute_write(
            """
            UPDATE card_settlements
            SET applied = 1,
                result = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE ref = ?
              AND applied = 0
              AND event_id <> ''
            """,
            (
                json.dumps(dict(result or {}), ensure_ascii=False, sort_keys=True),
                normalized_ref,
            ),
        )
        return int(cursor.rowcount or 0) == 1

    def project_applied_card_settlement(self, ref: str) -> int:
        """Refresh every session's card projection from an applied receipt."""
        row = self.get_card_settlement(ref)
        if row is None or int(row.get("applied", 0)) != 1:
            return 0
        terminal = {
            "confirm": "confirmed",
            "confirmed": "confirmed",
            "reject": "rejected",
            "rejected": "rejected",
            # A revise is not a plain rejection: the user replaced the wording
            # and accepted the corrected version, and a derived hypothesis was
            # written. Projecting it as "rejected" made the card read 已标记不准
            # right after the user said 我认可修正版.
            "revise": "revised",
            "revised": "revised",
        }.get(str(row.get("verdict", "")).strip().lower())
        if terminal is None:
            return 0
        cursor = self._execute_write(
            """
            UPDATE chat_turns
            SET payload = json_set(payload, '$.state', ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE json_valid(payload)
              AND json_extract(payload, '$.type') = 'card'
              AND json_extract(payload, '$.ref') = ?
              AND json_extract(payload, '$.state') IN ('pending', 'discussing', 'deferred')
            """,
            (terminal, ref),
        )
        return int(cursor.rowcount or 0)

    def list_dialogue_history(
        self,
        *,
        scopes: Sequence[str],
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return one cross-session completed cognition history."""
        normalized_scopes = [str(scope).strip() for scope in scopes if str(scope).strip()]
        if not normalized_scopes:
            return []
        self._ensure_fresh_read()
        placeholders = ", ".join("?" for _ in normalized_scopes)
        cursor = self.conn.execute(
            f"""
            SELECT turn_id, session, scope, subject_id, subject_title,
                   reply_to_turn_id, message,
                   status, reply, error, payload, created_at, updated_at
            FROM (
                   SELECT rowid AS insertion_rowid,
                       turn_id, session, scope, subject_id, subject_title,
                       reply_to_turn_id, message,
                       status, reply, error, payload, created_at, updated_at
                FROM chat_turns
                WHERE status = 'completed' AND scope IN ({placeholders})
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, insertion_rowid ASC
            """,
            [*normalized_scopes, max(1, int(limit))],
        )
        return [self._normalize_chat_turn_row(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Profile update ledger (append-only audit trail)
    # ------------------------------------------------------------------

    def insert_profile_ledger(
        self,
        *,
        write_point: str,
        source: str = "",
        before_summary: str = "",
        after_summary: str = "",
        diff: str = "",
        source_refs: Sequence[str] | None = None,
        outcome: str = "success",
        turn_id: str = "",
        gate_verdict: str = "",
        held_id: str = "",
        error: str = "",
        effect_key: str = "",
    ) -> int:
        """Append one profile write-point ledger row.

        Recorded AFTER the profile-mutating action finishes, with
        ``outcome`` = ``success`` | ``failed`` (r3/R2-6: a single INSERT
        per action, never a separate ``attempted`` pre-write). ``diff`` is
        truncated to 2000 chars by callers; ``source_refs`` is JSON-encoded
        here. Callers wrap this in try/except and log at WARNING — the
        ledger must never break the underlying write.
        """
        refs_json = json.dumps(list(source_refs or []), ensure_ascii=False, sort_keys=True)
        normalized_effect_key = _validated_profile_ledger_effect_key(effect_key)
        cursor = self._execute_write(
            """INSERT OR IGNORE INTO profile_update_ledger
               (effect_key, write_point, source, before_summary, after_summary,
                diff, source_refs, outcome, turn_id, gate_verdict, held_id, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                normalized_effect_key,
                str(write_point),
                str(source or ""),
                str(before_summary or ""),
                str(after_summary or ""),
                str(diff or ""),
                refs_json,
                str(outcome or "success"),
                str(turn_id or ""),
                str(gate_verdict or ""),
                str(held_id or ""),
                str(error or ""),
            ),
        )
        return (cursor.lastrowid or 0) if cursor.rowcount else 0

    def query_profile_ledger(
        self,
        *,
        days: int = 30,
        write_point: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent profile-ledger rows (newest-first) for the CLI.

        ``source_refs`` is returned decoded back into a list.
        """
        self._ensure_fresh_read()
        clauses = ["timestamp >= datetime('now', '-' || ? || ' day', 'localtime')"]
        params: list[Any] = [max(1, int(days))]
        if write_point:
            clauses.append("write_point = ?")
            params.append(write_point)
        params.append(max(1, int(limit)))
        cursor = self.conn.execute(
            f"""
            SELECT id, timestamp, effect_key, write_point, source, before_summary,
                   after_summary, diff, source_refs, outcome, turn_id,
                   gate_verdict, held_id, error
            FROM profile_update_ledger
            WHERE {" AND ".join(clauses)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        )
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            record = dict(row)
            raw_refs = record.get("source_refs") or "[]"
            try:
                decoded = json.loads(raw_refs)
            except (TypeError, ValueError):
                decoded = []
            record["source_refs"] = decoded if isinstance(decoded, list) else []
            rows.append(record)
        return rows

    def posture_gate_shadow_stats(self) -> dict[str, Any]:
        """Shadow-judgement statistics for the enforce save-time guard (Phase 3).

        A *valid* shadow judgement is a ledger row whose ``gate_verdict`` is one
        of ``shadow_accept`` / ``shadow_downgrade`` / ``shadow_reject`` (a
        ``shadow_error`` row is a provider failure, not a judgement, and does
        not count). Returns the earliest valid judgement timestamp plus valid
        counts within the last 14 and 7 days.
        """
        self._ensure_fresh_read()
        valid = ("shadow_accept", "shadow_downgrade", "shadow_reject")
        placeholders = ", ".join("?" for _ in valid)
        earliest_row = self.conn.execute(
            f"""SELECT MIN(timestamp) AS earliest FROM profile_update_ledger
                WHERE gate_verdict IN ({placeholders})""",
            valid,
        ).fetchone()
        earliest = (earliest_row["earliest"] if earliest_row else None) or ""

        def _count(days: int) -> int:
            row = self.conn.execute(
                f"""SELECT COUNT(*) AS c FROM profile_update_ledger
                    WHERE gate_verdict IN ({placeholders})
                      AND timestamp >= datetime('now', '-' || ? || ' day', 'localtime')""",
                (*valid, int(days)),
            ).fetchone()
            return int(row["c"]) if row else 0

        return {
            "earliest_valid_at": str(earliest),
            "valid_count_14d": _count(14),
            "valid_count_7d": _count(7),
        }

    # ------------------------------------------------------------------
    # Confusion objects (Phase 2)
    # ------------------------------------------------------------------

    def insert_confusion(
        self,
        *,
        source: str = "",
        topic: str = "",
        observation: str = "",
        interpretation: str = "",
        interpretation_confidence: float = 0.0,
        evidence_refs: Sequence[str] | None = None,
        status: str = "open",
        expires_at: str = "",
    ) -> int:
        """Insert a new confusion row (defaults to ``open``). Returns its id."""
        refs_json = json.dumps(list(evidence_refs or []), ensure_ascii=False, sort_keys=True)
        cursor = self._execute_write(
            """INSERT INTO confusions
               (status, source, topic, observation, interpretation,
                interpretation_confidence, evidence_refs, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(status or "open"),
                str(source or ""),
                str(topic or ""),
                str(observation or ""),
                str(interpretation or ""),
                float(interpretation_confidence or 0.0),
                refs_json,
                str(expires_at or "") or None,
            ),
        )
        return cursor.lastrowid or 0

    def get_confusion(self, confusion_id: int) -> dict[str, Any] | None:
        """Return one confusion row by id (or ``None``)."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            "SELECT * FROM confusions WHERE id = ?",
            (int(confusion_id),),
        )
        row = cursor.fetchone()
        return self._decode_confusion_row(dict(row)) if row is not None else None

    def list_confusions(
        self,
        *,
        statuses: Sequence[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return confusions filtered by ``statuses`` (newest-first)."""
        self._ensure_fresh_read()
        params: list[Any] = []
        clause = ""
        status_list = [str(s) for s in (statuses or []) if str(s)]
        if status_list:
            placeholders = ", ".join("?" for _ in status_list)
            clause = f"WHERE status IN ({placeholders})"
            params.extend(status_list)
        params.append(max(1, int(limit)))
        cursor = self.conn.execute(
            f"SELECT * FROM confusions {clause} ORDER BY id DESC LIMIT ?",
            params,
        )
        return [self._decode_confusion_row(dict(row)) for row in cursor.fetchall()]

    def claim_confusion_clarifying(
        self,
        confusion_id: int,
        *,
        ask_turn_id: str = "",
        asked_at: str = "",
    ) -> bool:
        """Atomically move an ``open`` confusion into ``clarifying``.

        The partial unique index ``idx_confusions_clarifying_unique`` allows at
        most one ``clarifying`` row across ALL connections, so a concurrent
        claim by another connection raises ``IntegrityError`` here and we return
        ``False`` (lost the race). A ``False`` also results when the row is not
        ``open`` (already claimed / resolved) — rowcount 0.
        """
        try:
            cursor = self._execute_write(
                """UPDATE confusions
                   SET status = 'clarifying',
                       ask_turn_id = ?,
                       asked_at = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = 'open'""",
                (str(ask_turn_id or ""), str(asked_at or "") or None, int(confusion_id)),
            )
        except sqlite3.IntegrityError:
            return False
        return cursor.rowcount > 0

    def release_orphan_confusion_claim(
        self,
        confusion_id: int,
        *,
        expected_ask_turn_id: str,
        minimum_age_seconds: float,
    ) -> bool:
        """Release an aged clarifying claim only when its turn still does not exist."""
        normalized_turn_id = str(expected_ask_turn_id or "")
        if (
            isinstance(minimum_age_seconds, bool)
            or not math.isfinite(minimum_age_seconds)
            or minimum_age_seconds < 0
        ):
            raise ValueError("minimum_age_seconds must be finite and non-negative")
        stale_modifier = f"-{minimum_age_seconds:.6f} seconds"
        cursor = self._execute_write(
            """
            UPDATE confusions
               SET status = 'open',
                   ask_turn_id = '',
                   asked_at = NULL,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
               AND status = 'clarifying'
               AND ask_turn_id = ?
               AND julianday(updated_at) <= julianday('now', ?)
               AND NOT EXISTS (
                    SELECT 1
                      FROM chat_turns
                     WHERE turn_id = ?
               )
            """,
            (
                int(confusion_id),
                normalized_turn_id,
                stale_modifier,
                normalized_turn_id,
            ),
        )
        return cursor.rowcount > 0

    def update_confusion(self, confusion_id: int, **fields: Any) -> None:
        """Update mutable confusion columns (whitelist-guarded)."""
        allowed = {
            "status",
            "topic",
            "interpretation",
            "interpretation_confidence",
            "resolution",
            "resolution_note",
            "asked_at",
            "ask_turn_id",
            "defer_count",
            "expires_at",
            "held_updates",
            "replay_queue",
            "resolved_at",
        }
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unknown confusion column: {key}")
            assignments.append(f"{key} = ?")
            if key in {"held_updates", "replay_queue"} and not isinstance(value, str):
                params.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
            else:
                params.append(value)
        if not assignments:
            return
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(confusion_id))
        self._execute_write(
            f"UPDATE confusions SET {', '.join(assignments)} WHERE id = ?",
            params,
        )

    @staticmethod
    def _decode_confusion_row(record: dict[str, Any]) -> dict[str, Any]:
        for json_key in ("evidence_refs", "held_updates", "replay_queue"):
            raw = record.get(json_key)
            if isinstance(raw, str) and raw.strip():
                try:
                    record[json_key] = json.loads(raw)
                except (TypeError, ValueError):
                    record[json_key] = []
            elif not isinstance(raw, list):
                record[json_key] = []
        return record

    def enqueue_confusion_replay(
        self,
        confusion_id: int,
        item: Mapping[str, object],
        *,
        max_items: int = 5,
    ) -> list[dict[str, Any]]:
        """Atomically append one replay item and return any oldest-first evictions."""
        limit = max(1, int(max_items))
        queued_item = dict(item)
        identity = str(queued_item.get("turn_id") or queued_item.get("replay_id") or "")
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT replay_queue FROM confusions WHERE id = ?",
                (int(confusion_id),),
            ).fetchone()
            if row is None:
                conn.rollback()
                return []
            queue = self._decode_confusion_replay_queue(row["replay_queue"])
            if identity and any(
                str(existing.get("turn_id") or existing.get("replay_id") or "") == identity
                for existing in queue
            ):
                conn.commit()
                return []
            queue.append(queued_item)
            dropped = queue[:-limit]
            queue = queue[-limit:]
            conn.execute(
                """UPDATE confusions
                   SET replay_queue = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    json.dumps(queue, ensure_ascii=False, sort_keys=True),
                    int(confusion_id),
                ),
            )
            conn.commit()
            return dropped
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def pop_confusion_replay_head(self, confusion_id: int, *, expected_id: str) -> bool:
        """Pop exactly the expected FIFO head; mismatch is a fencing failure."""
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT replay_queue FROM confusions WHERE id = ?",
                (int(confusion_id),),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            queue = self._decode_confusion_replay_queue(row["replay_queue"])
            if not queue:
                conn.commit()
                return False
            head = queue[0]
            head_id = str(head.get("turn_id") or head.get("replay_id") or "")
            if not expected_id or head_id != expected_id:
                conn.commit()
                return False
            conn.execute(
                """UPDATE confusions
                   SET replay_queue = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    json.dumps(queue[1:], ensure_ascii=False, sort_keys=True),
                    int(confusion_id),
                ),
            )
            conn.commit()
            return True
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def clear_confusion_replay_queue(self, confusion_id: int) -> list[dict[str, Any]]:
        """Atomically clear and return every queued attribution for release auditing."""
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT replay_queue FROM confusions WHERE id = ?",
                (int(confusion_id),),
            ).fetchone()
            if row is None:
                conn.rollback()
                return []
            queue = self._decode_confusion_replay_queue(row["replay_queue"])
            if queue:
                conn.execute(
                    """UPDATE confusions
                       SET replay_queue = '[]', updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (int(confusion_id),),
                )
            conn.commit()
            return queue
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def list_pending_confusion_dialogue_replays(
        self,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return durable queue heads plus completed-turn attribution gaps.

        A resolve commits before the historical, separate FIFO pop. If the
        process dies in that window the confusion is already terminal, so a
        ``status='clarifying'``-only scan can never see its non-empty queue.
        Queue recovery therefore covers *every* status; the narrower completed
        turn scan remains limited to clarifying rows with an empty queue.
        """
        self._ensure_fresh_read()
        normalized_limit = max(1, int(limit))
        queued_rows = self.conn.execute(
            """
            SELECT c.id AS confusion_id,
                   c.ask_turn_id AS ask_turn_id,
                   COALESCE(t.turn_id, json_extract(c.replay_queue, '$[0].turn_id'), '')
                       AS turn_id,
                   COALESCE(t.session, '') AS session,
                   COALESCE(t.subject_id, CAST(c.id AS TEXT)) AS subject_id,
                   COALESCE(t.subject_title, c.topic) AS subject_title,
                   COALESCE(t.message, '') AS message,
                   COALESCE(t.reply, '') AS reply,
                   COALESCE(t.created_at, c.created_at) AS created_at,
                   COALESCE(t.updated_at, c.updated_at) AS updated_at,
                   1 AS has_replay_queue
            FROM confusions AS c
            LEFT JOIN chat_turns AS t
              ON t.turn_id = json_extract(c.replay_queue, '$[0].turn_id')
            WHERE json_valid(c.replay_queue)
              AND json_array_length(c.replay_queue) > 0
            ORDER BY c.updated_at ASC, c.id ASC
            LIMIT ?
            """,
            (normalized_limit,),
        ).fetchall()
        remaining = max(0, normalized_limit - len(queued_rows))
        gap_rows = self.conn.execute(
            """
            SELECT c.id AS confusion_id,
                   c.ask_turn_id AS ask_turn_id,
                   t.turn_id, t.session, t.subject_id, t.subject_title,
                   t.message, t.reply, t.created_at, t.updated_at,
                   0 AS has_replay_queue
            FROM confusions AS c
            JOIN chat_turns AS t
              ON t.scope = 'confusion'
             AND t.subject_id = CAST(c.id AS TEXT)
            WHERE c.status = 'clarifying'
              AND (
                    NOT json_valid(c.replay_queue)
                    OR json_array_length(c.replay_queue) = 0
                  )
              AND t.status = 'completed'
              AND julianday(t.updated_at) > julianday(COALESCE(c.asked_at, c.created_at))
              AND CASE
                    WHEN json_valid(t.payload)
                    THEN COALESCE(
                        json_extract(t.payload, '$.confusion_anchor_processed'),
                        0
                    )
                    ELSE 0
                  END != 1
            ORDER BY t.updated_at ASC, t.rowid ASC
            LIMIT ?
            """,
            (remaining,),
        ).fetchall()
        rows = [dict(row) for row in queued_rows]
        rows.extend(dict(row) for row in gap_rows)
        rows.sort(key=lambda row: (str(row.get("updated_at", "")), str(row.get("turn_id", ""))))
        return rows[:normalized_limit]

    def mark_confusion_dialogue_replay_processed(self, turn_id: str) -> bool:
        """Receipt attribution in the turn payload, including while it is pending."""
        cursor = self._execute_write(
            """
            UPDATE chat_turns
            SET payload = json_set(
                    CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,
                    '$.confusion_anchor_processed',
                    1
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
              AND scope = 'confusion'
            """,
            (turn_id,),
        )
        return int(cursor.rowcount or 0) == 1

    @staticmethod
    def _decode_confusion_replay_queue(raw: object) -> list[dict[str, Any]]:
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                return []
        else:
            decoded = raw
        if not isinstance(decoded, list):
            return []
        return [dict(item) for item in decoded if isinstance(item, dict)]

    # ------------------------------------------------------------------
    # LLM usage ledger
    # ------------------------------------------------------------------

    def insert_llm_usage(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_cny: float,
        caller: str = "",
        success: bool = True,
        cached_input_tokens: int = 0,
    ) -> int:
        """Append one LLM-call usage record.

        ``cached_input_tokens`` (v0.3.28+) is the portion of
        ``prompt_tokens`` served from provider-side prompt cache —
        always ``<= prompt_tokens``. 0 means no cache use. Used by
        ``cost --by caller`` to compute hit rates and by
        ``estimate_cost`` to discount cached tokens correctly.
        """
        total = max(0, prompt_tokens) + max(0, completion_tokens)
        cursor = self._execute_write(
            """INSERT INTO llm_usage
               (provider, model, caller, prompt_tokens, completion_tokens,
                total_tokens, cached_input_tokens, estimated_cost_cny,
                success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider or "",
                model or "",
                caller or "",
                int(max(0, prompt_tokens)),
                int(max(0, completion_tokens)),
                int(total),
                int(max(0, cached_input_tokens)),
                float(estimated_cost_cny),
                1 if success else 0,
            ),
        )
        return cursor.lastrowid or 0

    # ------------------------------------------------------------------
    # Evaluator embedding-prefilter shadow audit
    # ------------------------------------------------------------------

    def record_prefilter_shadow_decisions(
        self,
        decisions: Sequence[Mapping[str, Any]],
    ) -> int:
        """Persist privacy-safe shadow decisions and enforce bounded retention.

        Callers treat this observer as best effort. Raising is intentional: the
        discovery engine catches any telemetry/storage error and keeps the
        candidate on the LLM path, while the incomplete evidence prevents the
        enforce gate from passing silently.
        """

        params: list[tuple[Any, ...]] = []
        for decision in decisions:
            validate_prefilter_storage_record(decision)
            params.append(
                (
                    str(decision["decision_id"]),
                    str(decision["candidate_hash"]),
                    str(decision["platform_class"]),
                    str(decision["context_class"]),
                    (None if decision.get("similarity") is None else float(decision["similarity"])),
                    float(decision["threshold"]),
                    1 if bool(decision["explore"]) else 0,
                    str(decision["embedding_namespace"]),
                    str(decision["profile_digest"]),
                    1 if bool(decision["would_filter"]) else 0,
                    str(decision["embedding_status"]),
                    1 if bool(decision["fail_open"]) else 0,
                    1 if bool(decision["explicit_strong_interest"]) else 0,
                )
            )
        if not params:
            return 0

        connection = self.conn
        changes_before = connection.total_changes
        self._execute_many_write(
            """
            INSERT OR IGNORE INTO evaluator_prefilter_shadow_audit (
                decision_id,
                candidate_hash,
                platform_class,
                context_class,
                similarity,
                threshold,
                explore,
                embedding_namespace,
                profile_digest,
                would_filter,
                embedding_status,
                fail_open,
                explicit_strong_interest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        inserted = max(0, connection.total_changes - changes_before)
        self._execute_write(
            """
            DELETE FROM evaluator_prefilter_shadow_audit
            WHERE created_at < datetime('now', ?)
            """,
            (f"-{PREFILTER_AUDIT_RETENTION_DAYS} days",),
        )
        self._execute_write(
            """
            DELETE FROM evaluator_prefilter_shadow_audit
            WHERE id IN (
                SELECT id
                FROM evaluator_prefilter_shadow_audit
                ORDER BY created_at DESC, id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (PREFILTER_AUDIT_MAX_ROWS,),
        )
        return inserted

    def complete_prefilter_shadow_decisions(
        self,
        outcomes: Sequence[Mapping[str, Any]],
    ) -> int:
        """Join eventual raw LLM scores and admission-threshold results."""

        params: list[tuple[Any, ...]] = []
        for outcome in outcomes:
            decision_id = str(outcome.get("decision_id") or "")
            if re.fullmatch(r"[0-9a-f]{32}", decision_id) is None:
                raise ValueError("prefilter audit outcome decision_id must be random hex")
            llm_score = float(outcome["llm_score"])
            admission_threshold = float(outcome["admission_threshold"])
            admission_result = outcome.get("admission_result")
            if not math.isfinite(llm_score) or not 0.0 <= llm_score <= 1.0:
                raise ValueError("prefilter audit LLM score must be finite in [0, 1]")
            if not math.isfinite(admission_threshold) or not 0.0 <= admission_threshold <= 1.0:
                raise ValueError("prefilter audit admission threshold must be finite in [0, 1]")
            if not isinstance(admission_result, bool):
                raise ValueError("prefilter audit admission result must be boolean")
            if admission_result != (llm_score >= admission_threshold):
                raise ValueError("prefilter audit admission result disagrees with score threshold")
            params.append(
                (
                    llm_score,
                    admission_threshold,
                    1 if admission_result else 0,
                    decision_id,
                )
            )
        if not params:
            return 0
        connection = self.conn
        changes_before = connection.total_changes
        self._execute_many_write(
            """
            UPDATE evaluator_prefilter_shadow_audit
            SET llm_score = ?,
                admission_threshold = ?,
                admission_result = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE decision_id = ?
              AND completed_at IS NULL
            """,
            params,
        )
        return max(0, connection.total_changes - changes_before)

    def query_prefilter_shadow_audit(
        self,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return retained privacy-safe rows for the pure §6.4 gate."""

        sql = """
            SELECT decision_id,
                   candidate_hash,
                   platform_class,
                   context_class,
                   similarity,
                   threshold,
                   explore,
                   embedding_namespace,
                   profile_digest,
                   would_filter,
                   embedding_status,
                   fail_open,
                   explicit_strong_interest,
                   llm_score,
                   admission_threshold,
                   admission_result,
                   created_at,
                   completed_at
            FROM evaluator_prefilter_shadow_audit
            ORDER BY id ASC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(0, int(limit)),)
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def prefilter_shadow_audit_counts(self) -> dict[str, int]:
        """Return the retained and score-joinable sample sizes."""

        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE
                       WHEN completed_at IS NOT NULL
                        AND llm_score IS NOT NULL
                        AND admission_threshold IS NOT NULL
                        AND admission_result IS NOT NULL
                       THEN 1 ELSE 0 END) AS joined,
                   SUM(CASE WHEN completed_at IS NULL THEN 1 ELSE 0 END) AS incomplete
            FROM evaluator_prefilter_shadow_audit
            """
        ).fetchone()
        if row is None:
            return {"total": 0, "joined": 0, "incomplete": 0}
        return {
            "total": int(row["total"] or 0),
            "joined": int(row["joined"] or 0),
            "incomplete": int(row["incomplete"] or 0),
        }

    # ------------------------------------------------------------------
    # Recommendation temporal-ranking shadow audit
    # ------------------------------------------------------------------

    @staticmethod
    def _temporal_audit_count_map(value: object, *, expected_total: int) -> dict[str, int]:
        """Validate one aggregate count map without accepting content identity."""

        if not isinstance(value, Mapping):
            raise ValueError("temporal ranking audit count map must be an object")
        normalized: dict[str, int] = {}
        for raw_key, raw_count in value.items():
            key = str(raw_key).strip().lower()
            if re.fullmatch(r"[a-z0-9_<>\-=]{1,32}", key) is None:
                raise ValueError("temporal ranking audit count-map key is invalid")
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                raise ValueError("temporal ranking audit counts must be non-negative integers")
            normalized[key] = raw_count
        if sum(normalized.values()) != expected_total:
            raise ValueError("temporal ranking audit count map has the wrong total")
        return dict(sorted(normalized.items()))

    def record_temporal_ranking_shadow_audit(self, audit: Mapping[str, Any]) -> int:
        """Persist one privacy-safe ranking counterfactual with bounded retention."""

        policy_version = str(audit.get("policy_version") or "")
        if policy_version != _TEMPORAL_RANKING_AUDIT_POLICY:
            raise ValueError("unsupported temporal ranking audit policy")

        def _count(name: str) -> int:
            value = audit.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"temporal ranking audit {name} must be non-negative")
            return value

        candidate_count = _count("candidate_count")
        parseable_count = _count("parseable_published_count")
        eligible_count = _count("bonus_eligible_count")
        if parseable_count > candidate_count or eligible_count > parseable_count:
            raise ValueError("temporal ranking audit coverage counts are inconsistent")

        class_counts = self._temporal_audit_count_map(
            audit.get("class_counts"),
            expected_total=candidate_count,
        )
        source_counts = self._temporal_audit_count_map(
            audit.get("source_counts"),
            expected_total=candidate_count,
        )
        age_bucket_counts = self._temporal_audit_count_map(
            audit.get("age_bucket_counts"),
            expected_total=candidate_count,
        )

        raw_top_k = audit.get("top_k_metrics")
        if not isinstance(raw_top_k, list) or len(raw_top_k) > 3:
            raise ValueError("temporal ranking audit top-k metrics must be a bounded list")
        scalar_count_keys = {
            "requested_top_k",
            "effective_top_k",
            "overlap_count",
            "positional_match_count",
            "baseline_parseable_published",
            "effective_parseable_published",
            "baseline_bonus_eligible",
            "effective_bonus_eligible",
        }
        count_map_keys = {
            "baseline_classes",
            "effective_classes",
            "entered_classes",
            "exited_classes",
            "baseline_sources",
            "effective_sources",
            "entered_sources",
            "exited_sources",
            "baseline_age_buckets",
            "effective_age_buckets",
            "entered_age_buckets",
            "exited_age_buckets",
        }
        allowed_keys = scalar_count_keys | count_map_keys | {"jaccard"}
        top_k_metrics: list[dict[str, object]] = []
        for raw_metric in raw_top_k:
            if not isinstance(raw_metric, Mapping) or set(raw_metric) != allowed_keys:
                raise ValueError("temporal ranking audit top-k metric schema is invalid")
            metric: dict[str, object] = {}
            normalized_scalar_counts: dict[str, int] = {}
            for key in scalar_count_keys:
                value = raw_metric.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("temporal ranking audit top-k counts must be non-negative")
                normalized_scalar_counts[key] = value
                metric[key] = value
            effective_top_k = normalized_scalar_counts["effective_top_k"]
            requested_top_k = normalized_scalar_counts["requested_top_k"]
            overlap_count = normalized_scalar_counts["overlap_count"]
            if (
                requested_top_k not in {10, 50, 100}
                or effective_top_k > min(requested_top_k, candidate_count)
                or overlap_count > effective_top_k
            ):
                raise ValueError("temporal ranking audit top-k bounds are inconsistent")
            raw_jaccard = raw_metric.get("jaccard")
            if isinstance(raw_jaccard, bool) or not isinstance(raw_jaccard, int | float):
                raise ValueError("temporal ranking audit jaccard must be numeric")
            jaccard = float(raw_jaccard)
            if not math.isfinite(jaccard) or not 0.0 <= jaccard <= 1.0:
                raise ValueError("temporal ranking audit jaccard must be finite in [0, 1]")
            metric["jaccard"] = jaccard
            changed = effective_top_k - overlap_count
            for key in count_map_keys:
                expected = changed if key.startswith(("entered_", "exited_")) else effective_top_k
                metric[key] = self._temporal_audit_count_map(
                    raw_metric.get(key),
                    expected_total=expected,
                )
            top_k_metrics.append(metric)

        cursor = self._execute_write(
            """
            INSERT INTO temporal_ranking_shadow_audit (
                policy_version,
                candidate_count,
                parseable_published_count,
                bonus_eligible_count,
                class_counts_json,
                source_counts_json,
                age_bucket_counts_json,
                top_k_metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy_version,
                candidate_count,
                parseable_count,
                eligible_count,
                json.dumps(class_counts, sort_keys=True, separators=(",", ":")),
                json.dumps(source_counts, sort_keys=True, separators=(",", ":")),
                json.dumps(age_bucket_counts, sort_keys=True, separators=(",", ":")),
                json.dumps(top_k_metrics, sort_keys=True, separators=(",", ":")),
            ),
        )
        self._execute_write(
            """
            DELETE FROM temporal_ranking_shadow_audit
            WHERE created_at < datetime('now', ?)
            """,
            (f"-{_TEMPORAL_RANKING_AUDIT_RETENTION_DAYS} days",),
        )
        self._execute_write(
            """
            DELETE FROM temporal_ranking_shadow_audit
            WHERE id IN (
                SELECT id
                FROM temporal_ranking_shadow_audit
                ORDER BY created_at DESC, id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (_TEMPORAL_RANKING_AUDIT_MAX_ROWS,),
        )
        return int(cursor.lastrowid or 0)

    def query_temporal_ranking_shadow_audit(
        self,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return retained aggregate-only temporal ranking shadows."""

        sql = """
            SELECT id,
                   policy_version,
                   candidate_count,
                   parseable_published_count,
                   bonus_eligible_count,
                   class_counts_json,
                   source_counts_json,
                   age_bucket_counts_json,
                   top_k_metrics_json,
                   created_at
            FROM temporal_ranking_shadow_audit
            ORDER BY id ASC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(0, int(limit)),)
        results: list[dict[str, Any]] = []
        for raw_row in self.conn.execute(sql, params).fetchall():
            row = dict(raw_row)
            row["class_counts"] = json.loads(str(row.pop("class_counts_json")))
            row["source_counts"] = json.loads(str(row.pop("source_counts_json")))
            row["age_bucket_counts"] = json.loads(str(row.pop("age_bucket_counts_json")))
            row["top_k_metrics"] = json.loads(str(row.pop("top_k_metrics_json")))
            results.append(row)
        return results

    def query_llm_usage_by_day(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-day aggregates for the last ``days`` days.

        Each row: {day, calls, prompt_tokens, completion_tokens,
        total_tokens, cost_cny}. Days with zero usage are omitted —
        the CLI fills gaps for display.
        """
        cursor = self.conn.execute(
            """
            SELECT date(timestamp, 'localtime') AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY day
            ORDER BY day DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_provider(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-(provider, model) totals over the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT provider,
                   model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY provider, model
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_caller(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-caller totals over the last ``days`` days.

        ``caller`` is a free-form string the LLM service tags into each
        row (e.g. ``discovery.evaluate`` / ``recommendation.write`` /
        ``soul.profile``). Untagged calls land under ``""`` which the
        CLI renders as ``(untagged)``. Result is sorted by cost so the
        first row is the most expensive caller.

        v0.3.28+ also returns ``cached_input_tokens`` so the CLI can
        compute and surface per-caller cache hit rates — a low rate
        (< 30%) signals prompt-prefix instability worth investigating.
        """
        cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_total(self, *, days: int = 7) -> dict[str, Any]:
        """Return a single-row total for the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            """,
            (max(1, int(days)),),
        )
        row = cursor.fetchone()
        return (
            dict(row)
            if row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

    def max_llm_usage_id(self) -> int:
        """Return the highest currently-stored ``llm_usage.id`` (0 if empty).

        Used as a checkpoint for "what's been billed since this point"
        queries — the init / discovery cycle wrappers snapshot it on
        entry and pass it to ``query_llm_usage_since_id`` on exit to
        scope the cost summary to that single phase.
        """
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM llm_usage")
        row = cursor.fetchone()
        return int(row["m"]) if row else 0

    def query_llm_usage_since_id(self, *, since_id: int) -> dict[str, Any]:
        """Return per-caller breakdown + totals for rows ``id > since_id``.

        Output: ``{"total": {calls, prompt_tokens, completion_tokens,
        cost_cny}, "by_caller": [{caller, calls, ...}, ...]}``. Bound
        to a single phase by passing ``max_llm_usage_id()`` taken at
        the phase entry.
        """
        total_cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            """,
            (int(since_id),),
        )
        total_row = total_cursor.fetchone()
        total = (
            dict(total_row)
            if total_row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

        caller_cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (int(since_id),),
        )
        return {
            "total": total,
            "by_caller": [dict(row) for row in caller_cursor.fetchall()],
        }

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        keyword: str = "",
        limit: int = 100,
        satisfaction_modes: frozenset[str] | None = None,
        after_event_id: int | None = None,
        include_profile_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Query events with optional filters.

        ``satisfaction_modes`` filters by ``inferred_satisfaction``. When
        the set includes ``"unknown"``, rows with a NULL classification
        (pre-migration legacy rows) are also returned.

        ``after_event_id`` restricts to rows with ``id`` strictly greater
        than the given watermark — used by the cognition cycle to read only
        events not yet folded into awareness. Result order is unchanged
        (newest-first); callers that need chronological order reverse it.
        """
        sql = "SELECT * FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        # Account-scoped source imports remain durable evidence when a user
        # switches accounts, but they must stop feeding cognition/profile
        # rebuilds.  Explicit diagnostics can opt back in; normal consumers
        # fail closed over rows marked inactive by the identity activator.
        if not include_profile_inactive:
            clauses.append(
                "(metadata IS NULL OR json_valid(metadata) = 0 "
                "OR COALESCE(json_extract(metadata, '$.profile_inactive'), 0) <> 1)"
            )

        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if after_event_id is not None:
            clauses.append("id > ?")
            params.append(after_event_id)

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if keyword:
            like = f"%{keyword}%"
            clauses.append("(url LIKE ? OR title LIKE ? OR metadata LIKE ?)")
            params.extend([like, like, like])

        if satisfaction_modes is not None:
            modes = list(satisfaction_modes)
            mode_clauses: list[str] = []
            if modes:
                placeholders = ", ".join("?" for _ in modes)
                mode_clauses.append(f"inferred_satisfaction IN ({placeholders})")
                params.extend(modes)
            if "unknown" in satisfaction_modes:
                mode_clauses.append("inferred_satisfaction IS NULL")
            if mode_clauses:
                clauses.append("(" + " OR ".join(mode_clauses) + ")")
            else:
                # Empty modes set explicitly requested → match nothing.
                clauses.append("1 = 0")

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def recent_event_urls(
        self,
        event_types: list[str],
        *,
        within_hours: int,
        exclude_source: str | None = None,
        limit: int = 2000,
    ) -> set[str]:
        """Return the non-empty ``url`` values of recent events of the given types.

        Thin wrapper over :meth:`query_events` used by account_sync's
        cross-source dedup: events observed by the browser extension are
        looked up here so a second (history/favorites/following/X) pull of
        the same observation is not re-emitted.

        ``exclude_source`` drops rows whose ``metadata.source`` equals the
        given value (JSON parsed per row). account_sync always passes
        ``exclude_source="account_sync"`` so its own prior rows never
        suppress a genuine re-observation seen only via the pulled API.
        """
        from datetime import UTC, datetime, timedelta

        start_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=within_hours)
        rows = self.query_events(
            event_types=event_types,
            start_time=start_time,
            limit=limit,
        )
        urls: set[str] = set()
        for row in rows:
            url = str(row.get("url", "") or "").strip()
            if not url:
                continue
            if exclude_source is not None and self._event_source(row) == exclude_source:
                continue
            urls.add(url)
        return urls

    @staticmethod
    def _event_source(row: dict[str, Any]) -> str:
        """Extract ``metadata.source`` from a raw event row (JSON string or dict)."""
        metadata_raw = row.get("metadata")
        if isinstance(metadata_raw, dict):
            return str(metadata_raw.get("source", "") or "")
        if isinstance(metadata_raw, str) and metadata_raw:
            try:
                parsed = json.loads(metadata_raw)
            except (ValueError, TypeError):
                return ""
            if isinstance(parsed, dict):
                return str(parsed.get("source", "") or "")
        return ""

    def count_events_by_type(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        """Count events grouped by event type."""
        sql = "SELECT event_type, COUNT(*) AS count FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} GROUP BY event_type ORDER BY event_type ASC"
        cursor = self.conn.execute(sql, params)
        return {str(row["event_type"]): int(row["count"]) for row in cursor.fetchall()}

    def search_local_inspiration_evidence(
        self,
        query: str,
        *,
        limit: int = 10,
        lookback_days: int = 30,
    ) -> list[dict[str, object]]:
        """Return local content evidence for inspiration grounding."""

        clean_query = str(query or "").strip()
        if not clean_query:
            return []
        tokens = _local_evidence_tokens(clean_query)
        if not tokens:
            return []

        like_terms = [f"%{_escape_like_term(token)}%" for token in tokens[:12]]
        where = " OR ".join(
            "title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\'" for _ in like_terms
        )
        params: list[object] = []
        for term in like_terms:
            params.extend([term, term])
        params.append(f"-{max(1, int(lookback_days))} days")

        rows = self.conn.execute(
            f"""
            SELECT
                title,
                COALESCE(
                    NULLIF(content_url, ''),
                    CASE
                        WHEN COALESCE(bvid, '') != ''
                        THEN 'https://www.bilibili.com/video/' || bvid
                        ELSE ''
                    END
                ) AS url,
                description,
                source_platform,
                content_id,
                pool_topic_label AS topic_label,
                discovered_at AS created_at
            FROM content_cache
            WHERE ({where})
              AND COALESCE(pool_status, '') NOT IN ('purged_by_dislike', 'purged_by_reinit')
              AND datetime(COALESCE(NULLIF(discovered_at, ''), '1970-01-01'))
                  >= datetime('now', ?)
            ORDER BY discovered_at DESC
            LIMIT 200
            """,
            params,
        ).fetchall()

        scored: list[tuple[int, str, dict[str, object]]] = []
        for row in rows:
            title = str(row["title"] or "").strip()
            url = str(row["url"] or "").strip()
            if not title or not url:
                continue
            description = str(row["description"] or "").strip()
            haystack = f"{title} {description}"
            match_count = sum(1 for token in tokens if token in haystack)
            if len(tokens) >= 2 and match_count < 2 and clean_query not in haystack:
                continue
            scored.append(
                (
                    match_count,
                    str(row["created_at"] or ""),
                    {
                        "title": title,
                        "url": url,
                        "highlights": [description] if description else [],
                        "source_table": "content_cache",
                        "source_platform": str(row["source_platform"] or ""),
                        "content_id": str(row["content_id"] or ""),
                        "topic_label": str(row["topic_label"] or ""),
                        "created_at": str(row["created_at"] or ""),
                    },
                )
            )
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [payload for _, _, payload in scored[: max(1, int(limit))]]

    def cache_content(self, bvid: str, **kwargs: Any) -> CacheContentWriteResult:
        """Cache discovered content and return the lock-held admission outcome.

        Args:
            bvid: Video BV ID.
            **kwargs: Content fields.
        """
        import json

        published = normalize_published_time(
            kwargs.get("published_at"),
            label=kwargs.get("published_label"),
        )
        temporal_fields_present = all(
            field in kwargs
            for field in ("temporal_class", "temporal_confidence", "temporal_reason")
        )
        incoming_evidence_complete = is_complete_temporal_evidence_marker(
            kwargs.get(
                "temporal_evidence_complete",
                kwargs.get("temporal_evaluated", temporal_fields_present),
            )
        )
        incoming_lifecycle_source = dict(kwargs)
        incoming_lifecycle_source["temporal_evidence_complete"] = incoming_evidence_complete
        incoming_review_clock = _parse_temporal_storage_clock(kwargs.get("temporal_evaluated_at"))
        incoming_content_text = {
            "title": kwargs.get("title", ""),
            "description": kwargs.get("description", ""),
            "body_text": kwargs.get("body_text", ""),
            "published_label": published.published_label,
        }
        incoming_grounding_downgraded = _temporal_grounding_was_downgraded(
            incoming_lifecycle_source,
            content_text=incoming_content_text,
        )
        temporal, incoming_lifecycle = _normalize_temporal_evidence_group_for_storage(
            incoming_lifecycle_source,
            content_text=incoming_content_text,
        )
        # ``DiscoveredContent.to_cache_kwargs()`` always carries neutral
        # dataclass defaults.  Only complete, non-neutral evaluator evidence
        # may replace a durable classification; malformed or explicit
        # ``unknown`` results must not wash a prior hard-gate decision away.
        has_explicit_temporal = (
            temporal_fields_present and temporal[0] != "unknown" and bool(incoming_lifecycle[7])
        )
        source_platform = str(kwargs.get("source_platform", "bilibili") or "").strip()
        raw_content_id = str(kwargs.get("content_id", bvid) or "").strip()
        identity_content_id = raw_content_id if source_platform else bvid.strip()
        item_key = str(kwargs.get("item_key", "") or "").strip() or make_item_key(
            source_platform or "bilibili",
            identity_content_id,
            str(kwargs.get("content_url", "") or ""),
        )

        def _cache_write_params(
            connection: sqlite3.Connection,
        ) -> _CacheContentPreparedWrite:
            existing_identity_row = connection.execute(
                "SELECT bvid FROM content_cache WHERE item_key = ?",
                (item_key,),
            ).fetchone()
            resolved_bvid = (
                str(existing_identity_row["bvid"]) if existing_identity_row is not None else bvid
            )
            existing_temporal_row = connection.execute(
                """
                SELECT
                    published_at,
                    published_label,
                    temporal_class,
                    temporal_confidence,
                    temporal_reason,
                    temporal_policy_version,
                    temporal_validity_mode,
                    temporal_valid_until,
                    temporal_scope,
                    temporal_state,
                    temporal_evidence,
                    temporal_next_review_at,
                    temporal_evaluated_at,
                    temporal_evidence_complete,
                    temporal_review_attempts,
                    temporal_review_retry_at,
                    pool_status
                FROM content_cache
                WHERE bvid = ?
                LIMIT 1
                """,
                (resolved_bvid,),
            ).fetchone()
            merged_published_at = published.published_at
            merged_published_label = published.published_label
            merged_temporal_class: object = temporal[0]
            merged_temporal_confidence: object = temporal[1]
            temporal_to_persist = temporal
            temporal_lifecycle_to_persist = incoming_lifecycle
            persist_temporal_fields = has_explicit_temporal
            renewed_review_retry_at = ""
            if existing_temporal_row is not None:
                existing_temporal = _normalize_temporal_fields_for_storage(
                    temporal_class=existing_temporal_row["temporal_class"],
                    temporal_confidence=existing_temporal_row["temporal_confidence"],
                    temporal_reason=existing_temporal_row["temporal_reason"],
                    temporal_policy_version=existing_temporal_row["temporal_policy_version"],
                )
                existing_has_temporal_evidence = existing_temporal[0] != "unknown"
                _, existing_lifecycle = _normalize_temporal_evidence_group_for_storage(
                    dict(existing_temporal_row)
                )
                if existing_has_temporal_evidence or has_explicit_temporal:
                    merged_published_at, merged_published_label = (
                        _merge_publication_evidence_conservatively(
                            existing_at=existing_temporal_row["published_at"],
                            existing_label=existing_temporal_row["published_label"],
                            incoming_at=published.published_at,
                            incoming_label=published.published_label,
                        )
                    )
                else:
                    merged_published_at = published.published_at or str(
                        existing_temporal_row["published_at"] or ""
                    )
                    merged_published_label = published.published_label or str(
                        existing_temporal_row["published_label"] or ""
                    )
                incoming_replaces_existing = has_explicit_temporal and (
                    not existing_has_temporal_evidence
                    or (
                        _temporal_evidence_is_authoritative_replacement(
                            temporal,
                            incoming_lifecycle,
                        )
                        and not incoming_grounding_downgraded
                    )
                )
                temporal_to_persist = temporal if incoming_replaces_existing else existing_temporal
                temporal_lifecycle_to_persist = (
                    incoming_lifecycle
                    if incoming_replaces_existing or not existing_has_temporal_evidence
                    else existing_lifecycle
                )
                merged_temporal_class = temporal_to_persist[0]
                merged_temporal_confidence = temporal_to_persist[1]
                # Repair a legacy/corrupt partial triple (for example a
                # missing reason) to the same fail-neutral tuple used by this
                # lock-held decision, so later readers cannot reinterpret it.
                persist_temporal_fields = True
            decision_now = datetime.now(UTC)
            temporal_decision = evaluate_temporal_eligibility(
                temporal_class=merged_temporal_class,
                temporal_confidence=merged_temporal_confidence,
                published_at=merged_published_at,
                temporal_validity_mode=temporal_lifecycle_to_persist[0],
                temporal_valid_until=temporal_lifecycle_to_persist[1],
                temporal_scope=temporal_lifecycle_to_persist[2],
                temporal_state=temporal_lifecycle_to_persist[3],
                temporal_evidence=temporal_lifecycle_to_persist[4],
                temporal_next_review_at=temporal_lifecycle_to_persist[5],
                temporal_evaluated_at=temporal_lifecycle_to_persist[6],
                evidence_complete=bool(temporal_lifecycle_to_persist[7]),
                temporal_policy_version=temporal_to_persist[3],
                now=decision_now,
            )
            temporal_disposition = _temporal_decision_disposition(temporal_decision)
            if existing_temporal_row is not None:
                existing_review_clock = _parse_temporal_storage_clock(
                    existing_temporal_row["temporal_evaluated_at"]
                )
                existing_retry_clock = _parse_temporal_storage_clock(
                    existing_temporal_row["temporal_review_retry_at"]
                )
                completed_new_review = bool(
                    incoming_review_clock is not None
                    and incoming_review_clock != existing_review_clock
                )
                if (
                    str(existing_temporal_row["pool_status"] or "fresh") == "temporal_review_hold"
                    and temporal_disposition == "review_due"
                    and completed_new_review
                    and (existing_retry_clock is None or existing_retry_clock <= decision_now)
                ):
                    renewed_review_retry_at = _temporal_review_retry_text(
                        now=decision_now,
                        attempts=max(
                            1,
                            int(existing_temporal_row["temporal_review_attempts"] or 0),
                        ),
                    )
            params = (
                resolved_bvid,
                item_key,
                kwargs.get("title", ""),
                kwargs.get("up_name", ""),
                kwargs.get("up_mid", 0),
                kwargs.get("duration", 0),
                json.dumps(kwargs.get("tags", []), ensure_ascii=False),
                kwargs.get("topic_key", ""),
                kwargs.get("topic_group", ""),
                _normalize_style_key_for_storage(kwargs.get("style_key", "")),
                kwargs.get("franchise_key", ""),
                kwargs.get("description", ""),
                merged_published_at,
                merged_published_label,
                kwargs.get("cover_url", ""),
                kwargs.get("view_count", 0),
                kwargs.get("like_count", 0),
                kwargs.get("favorite_count", 0),
                kwargs.get("collect_count", 0),
                kwargs.get("comment_count", 0),
                kwargs.get("share_count", 0),
                kwargs.get("danmaku_count", 0),
                kwargs.get("reply_count", 0),
                kwargs.get("retweet_count", 0),
                kwargs.get("bookmark_count", 0),
                kwargs.get("relevance_score", 0.0),
                kwargs.get("relevance_reason", ""),
                temporal_to_persist[0],
                temporal_to_persist[1],
                temporal_to_persist[2],
                temporal_to_persist[3],
                *temporal_lifecycle_to_persist,
                _pool_status_for_temporal_decision(temporal_decision),
                kwargs.get("pool_expression", ""),
                kwargs.get("pool_topic_label", ""),
                kwargs.get("candidate_tier", "primary"),
                kwargs.get("source", ""),
                kwargs.get("content_id", resolved_bvid),
                kwargs.get("content_url", ""),
                kwargs.get("source_platform", "bilibili"),
                kwargs.get("author_name", ""),
                kwargs.get("body_text", ""),
                kwargs.get("content_type", "video") or "video",
                self._coerce_source_keyword_id(kwargs.get("source_keyword_id")),
                float(kwargs.get("rating_score", 0.0) or 0.0),
                max(0, int(kwargs.get("rating_count", 0) or 0)),
                max(0, int(kwargs.get("source_rank", 0) or 0)),
                kwargs.get("up_level", 0),
                kwargs.get("follower_count", 0),
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                persist_temporal_fields,
                has_explicit_temporal,
                temporal_disposition,
                has_explicit_temporal,
                temporal_disposition,
                temporal_disposition,
                temporal_disposition,
                has_explicit_temporal,
                EXPLORE_STRATEGY,
                EXPLORE_ADMISSION_MIN_SCORE,
                self._pool_admission_min_score(),
            )
            return _CacheContentPreparedWrite(
                params=params,
                bvid=resolved_bvid,
                created=existing_temporal_row is None,
                temporal_decision=temporal_decision,
                renewed_review_retry_at=renewed_review_retry_at,
            )

        return self._execute_prepared_write(
            """
            INSERT INTO content_cache (
                bvid,
                item_key,
                title,
                up_name,
                up_mid,
                duration,
                tags,
                topic_key,
                topic_group,
                style_key,
                franchise_key,
                description,
                published_at,
                published_label,
                cover_url,
                view_count,
                like_count,
                favorite_count,
                collect_count,
                comment_count,
                share_count,
                danmaku_count,
                reply_count,
                retweet_count,
                bookmark_count,
                relevance_score,
                relevance_reason,
                temporal_class,
                temporal_confidence,
                temporal_reason,
                temporal_policy_version,
                temporal_validity_mode,
                temporal_valid_until,
                temporal_scope,
                temporal_state,
                temporal_evidence,
                temporal_next_review_at,
                temporal_evaluated_at,
                temporal_evidence_complete,
                pool_status,
                pool_expression,
                pool_topic_label,
                candidate_tier,
                last_scored_at,
                source,
                content_id,
                content_url,
                source_platform,
                author_name,
                body_text,
                content_type,
                source_keyword_id,
                rating_score,
                rating_count,
                source_rank,
                up_level,
                follower_count
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(bvid) DO UPDATE SET
                title = excluded.title,
                up_name = excluded.up_name,
                up_mid = excluded.up_mid,
                duration = excluded.duration,
                tags = excluded.tags,
                -- Preserve LLM-classified fields: when the incoming value
                -- is empty/zero, keep the existing DB value.  This prevents
                -- re-ingest from raw sources (e.g. xhs extension re-sending
                -- the same notes on every page load) from wiping out
                -- classifications that classify_pool_backlog has written.
                topic_key = COALESCE(
                    NULLIF(excluded.topic_key, ''),
                    content_cache.topic_key,
                    ''
                ),
                topic_group = COALESCE(
                    NULLIF(excluded.topic_group, ''),
                    content_cache.topic_group,
                    ''
                ),
                style_key = COALESCE(
                    NULLIF(excluded.style_key, ''),
                    content_cache.style_key,
                    ''
                ),
                franchise_key = COALESCE(
                    NULLIF(excluded.franchise_key, ''),
                    content_cache.franchise_key,
                    ''
                ),
                description = excluded.description,
                -- Publication and temporal evidence are merged under the
                -- writer lock.  Assign the prepared pair as one unit so a
                -- later raw timestamp cannot resurrect an expired item.
                published_at = excluded.published_at,
                published_label = excluded.published_label,
                cover_url = COALESCE(
                    NULLIF(excluded.cover_url, ''),
                    content_cache.cover_url,
                    ''
                ),
                view_count = excluded.view_count,
                like_count = excluded.like_count,
                favorite_count = excluded.favorite_count,
                collect_count = excluded.collect_count,
                comment_count = excluded.comment_count,
                share_count = excluded.share_count,
                danmaku_count = excluded.danmaku_count,
                reply_count = excluded.reply_count,
                retweet_count = excluded.retweet_count,
                bookmark_count = excluded.bookmark_count,
                relevance_score = CASE
                    WHEN excluded.relevance_score > 0 THEN excluded.relevance_score
                    ELSE COALESCE(content_cache.relevance_score, 0)
                END,
                relevance_reason = COALESCE(
                    NULLIF(excluded.relevance_reason, ''),
                    content_cache.relevance_reason,
                    ''
                ),
                temporal_class = CASE
                    WHEN ? THEN excluded.temporal_class
                    ELSE COALESCE(content_cache.temporal_class, 'unknown')
                END,
                temporal_confidence = CASE
                    WHEN ? THEN excluded.temporal_confidence
                    ELSE COALESCE(content_cache.temporal_confidence, 0.0)
                END,
                temporal_reason = CASE
                    WHEN ? THEN excluded.temporal_reason
                    ELSE COALESCE(content_cache.temporal_reason, '')
                END,
                temporal_policy_version = CASE
                    WHEN ? THEN excluded.temporal_policy_version
                    ELSE COALESCE(
                        NULLIF(content_cache.temporal_policy_version, ''),
                        'v1'
                    )
                END,
                temporal_validity_mode = CASE
                    WHEN ? THEN excluded.temporal_validity_mode
                    ELSE COALESCE(content_cache.temporal_validity_mode, 'none')
                END,
                temporal_valid_until = CASE
                    WHEN ? THEN excluded.temporal_valid_until
                    ELSE COALESCE(content_cache.temporal_valid_until, '')
                END,
                temporal_scope = CASE
                    WHEN ? THEN excluded.temporal_scope
                    ELSE COALESCE(content_cache.temporal_scope, 'none')
                END,
                temporal_state = CASE
                    WHEN ? THEN excluded.temporal_state
                    ELSE COALESCE(content_cache.temporal_state, 'unknown')
                END,
                temporal_evidence = CASE
                    WHEN ? THEN excluded.temporal_evidence
                    ELSE COALESCE(content_cache.temporal_evidence, '')
                END,
                temporal_next_review_at = CASE
                    WHEN ? THEN excluded.temporal_next_review_at
                    ELSE COALESCE(content_cache.temporal_next_review_at, '')
                END,
                temporal_evaluated_at = CASE
                    WHEN ? THEN excluded.temporal_evaluated_at
                    ELSE COALESCE(content_cache.temporal_evaluated_at, '')
                END,
                temporal_evidence_complete = CASE
                    WHEN ? THEN excluded.temporal_evidence_complete
                    ELSE COALESCE(content_cache.temporal_evidence_complete, 0)
                END,
                temporal_review_attempts = CASE
                    WHEN ? AND ? != 'review_due' THEN 0
                    ELSE COALESCE(content_cache.temporal_review_attempts, 0)
                END,
                temporal_review_retry_at = CASE
                    WHEN ? AND ? != 'review_due' THEN ''
                    ELSE COALESCE(content_cache.temporal_review_retry_at, '')
                END,
                pool_expression = COALESCE(
                    NULLIF(excluded.pool_expression, ''),
                    content_cache.pool_expression,
                    ''
                ),
                pool_topic_label = COALESCE(
                    NULLIF(excluded.pool_topic_label, ''),
                    content_cache.pool_topic_label,
                    ''
                ),
                candidate_tier = excluded.candidate_tier,
                last_scored_at = CURRENT_TIMESTAMP,
                -- Re-fresh items previously trim-suppressed: 'suppressed' is
                -- an internal diversity decision (over-quota cuts, topic cap),
                -- not a user signal. When a discovery strategy re-finds the
                -- item it deserves another shot. Without this, B站 trending
                -- (which churns slowly) stays bottlenecked because most hot
                -- BVIDs are already cached as 'suppressed' from earlier
                -- trim cycles. User-driven states ('shown', 'feedbacked',
                -- 'purged_by_dislike') are preserved. Low-score suppressed
                -- rows only revive after a fresh/effective score meets the
                -- unified admission floor.
                pool_status = CASE
                    WHEN ? = 'expired'
                         AND COALESCE(content_cache.pool_status, 'fresh')
                             IN ('fresh', 'suppressed', 'temporal_review_hold')
                    THEN 'stale'
                    WHEN ? = 'review_due'
                         AND COALESCE(content_cache.pool_status, 'fresh')
                             IN ('fresh', 'suppressed', 'temporal_review_hold')
                    THEN 'temporal_review_hold'
                    WHEN content_cache.pool_status = 'temporal_review_hold'
                         AND ?
                    THEN 'fresh'
                    WHEN content_cache.pool_status = 'suppressed'
                         AND (
                            CASE
                                WHEN excluded.relevance_score > 0 THEN excluded.relevance_score
                                ELSE COALESCE(content_cache.relevance_score, 0)
                            END
                         ) >= CASE
                            WHEN LOWER(TRIM(COALESCE(excluded.source, ''))) = ? THEN ?
                            ELSE ?
                         END
                    THEN 'fresh'
                    ELSE content_cache.pool_status
                END,
                source = excluded.source,
                item_key = excluded.item_key,
                content_id = excluded.content_id,
                content_url = excluded.content_url,
                source_platform = excluded.source_platform,
                author_name = COALESCE(
                    NULLIF(excluded.author_name, ''),
                    content_cache.author_name,
                    ''
                ),
                rating_score = excluded.rating_score,
                rating_count = excluded.rating_count,
                source_rank = excluded.source_rank,
                up_level = excluded.up_level,
                follower_count = excluded.follower_count
            """,
            _cache_write_params,
        )

    @staticmethod
    def _coerce_source_keyword_id(value: Any) -> int | None:
        """Normalize a ``source_keyword_id`` kwarg to ``int`` or ``None``.

        Tolerates the field being absent / blank / non-numeric so any caller
        that has not been threaded through the P1.8 provenance path stays a
        plain NULL write (no behavior change vs. the pre-P1.8 schema).
        """
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _candidate_value(candidate: object, key: str, default: Any = "") -> Any:
        if isinstance(candidate, Mapping):
            return candidate.get(key, default)
        return getattr(candidate, key, default)

    @staticmethod
    def _candidate_json_payload(value: object, *, default: object) -> str:
        if isinstance(value, str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                return json.dumps(default, ensure_ascii=False)
            return value
        try:
            return json.dumps(default if value is None else value, ensure_ascii=False)
        except TypeError:
            return json.dumps(default, ensure_ascii=False)

    def enqueue_discovery_candidates(
        self,
        candidates: Sequence[Any],
        *,
        max_pending_per_source: int | None = None,
    ) -> int:
        """Insert raw discovery candidates into the pending evaluation queue.

        Existing ``candidate_key`` rows are treated as rediscovery signals: the
        row is not duplicated, but ``last_seen_at`` is refreshed so active
        sources do not look stale.
        """

        inserted = 0
        filtered_by_date = 0
        touched_sources: set[str] = set()
        for candidate in candidates:
            candidate_key = str(self._candidate_value(candidate, "candidate_key", "") or "").strip()
            if not candidate_key:
                continue
            source_platform = str(self._candidate_value(candidate, "source_platform", "") or "")
            if not self._source_publication_date_candidate_is_eligible(
                source_platform,
                self._candidate_value(candidate, "published_at", ""),
            ):
                filtered_by_date += 1
                continue
            tags = self._candidate_json_payload(
                self._candidate_value(candidate, "tags", []),
                default=[],
            )
            raw_payload = self._candidate_json_payload(
                self._candidate_value(candidate, "raw_payload", {}),
                default={},
            )
            published = normalize_published_time(
                self._candidate_value(candidate, "published_at", ""),
                label=self._candidate_value(candidate, "published_label", ""),
            )
            score_threshold = float(self._candidate_value(candidate, "score_threshold", 0.0) or 0.0)
            cursor = self._execute_write(
                """
                INSERT OR IGNORE INTO discovery_candidates (
                    candidate_key,
                    status,
                    source_platform,
                    source_strategy,
                    source_context,
                    content_type,
                    body_text,
                    bvid,
                    content_id,
                    content_url,
                    title,
                    author_name,
                    up_name,
                    up_mid,
                    description,
                    published_at,
                    published_label,
                    cover_url,
                    duration,
                    view_count,
                    like_count,
                    favorite_count,
                    collect_count,
                    comment_count,
                    share_count,
                    danmaku_count,
                    reply_count,
                    retweet_count,
                    bookmark_count,
                    tags,
                    candidate_tier,
                    score_threshold,
                    raw_payload,
                    source_keyword_id,
                    rating_score,
                    rating_count,
                    source_rank
                )
                VALUES (
                    ?, 'pending_eval', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    candidate_key,
                    source_platform,
                    str(self._candidate_value(candidate, "source_strategy", "") or ""),
                    str(self._candidate_value(candidate, "source_context", "") or ""),
                    str(self._candidate_value(candidate, "content_type", "video") or "video"),
                    str(self._candidate_value(candidate, "body_text", "") or ""),
                    str(self._candidate_value(candidate, "bvid", "") or ""),
                    str(self._candidate_value(candidate, "content_id", "") or ""),
                    str(self._candidate_value(candidate, "content_url", "") or ""),
                    str(self._candidate_value(candidate, "title", "") or ""),
                    str(self._candidate_value(candidate, "author_name", "") or ""),
                    str(self._candidate_value(candidate, "up_name", "") or ""),
                    int(self._candidate_value(candidate, "up_mid", 0) or 0),
                    str(self._candidate_value(candidate, "description", "") or ""),
                    published.published_at,
                    published.published_label,
                    str(self._candidate_value(candidate, "cover_url", "") or ""),
                    int(self._candidate_value(candidate, "duration", 0) or 0),
                    int(self._candidate_value(candidate, "view_count", 0) or 0),
                    int(self._candidate_value(candidate, "like_count", 0) or 0),
                    int(self._candidate_value(candidate, "favorite_count", 0) or 0),
                    int(self._candidate_value(candidate, "collect_count", 0) or 0),
                    int(self._candidate_value(candidate, "comment_count", 0) or 0),
                    int(self._candidate_value(candidate, "share_count", 0) or 0),
                    int(self._candidate_value(candidate, "danmaku_count", 0) or 0),
                    int(self._candidate_value(candidate, "reply_count", 0) or 0),
                    int(self._candidate_value(candidate, "retweet_count", 0) or 0),
                    int(self._candidate_value(candidate, "bookmark_count", 0) or 0),
                    tags,
                    str(self._candidate_value(candidate, "candidate_tier", "primary") or "primary"),
                    score_threshold,
                    raw_payload,
                    self._coerce_source_keyword_id(
                        self._candidate_value(candidate, "source_keyword_id", None)
                    ),
                    float(self._candidate_value(candidate, "rating_score", 0.0) or 0.0),
                    max(0, int(self._candidate_value(candidate, "rating_count", 0) or 0)),
                    max(0, int(self._candidate_value(candidate, "source_rank", 0) or 0)),
                ),
            )
            if source_platform:
                touched_sources.add(source_platform)
            if cursor.rowcount > 0:
                inserted += 1
                continue
            self._execute_write(
                """
                UPDATE discovery_candidates
                SET last_seen_at = CURRENT_TIMESTAMP,
                    -- Publication is part of the evaluation snapshot.  Raw
                    -- rediscovery may refine it only before a row is claimed;
                    -- otherwise complete_claim could cache evidence from a
                    -- different clock than the durable candidate row.
                    published_at = CASE
                        WHEN status = 'pending_eval'
                             AND COALESCE(temporal_evidence_complete, 0) = 0
                        THEN COALESCE(NULLIF(?, ''), published_at, '')
                        ELSE COALESCE(published_at, '')
                    END,
                    published_label = CASE
                        WHEN status = 'pending_eval'
                             AND COALESCE(temporal_evidence_complete, 0) = 0
                        THEN COALESCE(NULLIF(?, ''), published_label, '')
                        ELSE COALESCE(published_label, '')
                    END,
                    rating_score = ?,
                    rating_count = ?,
                    source_rank = ?
                WHERE candidate_key = ?
                """,
                (
                    published.published_at,
                    published.published_label,
                    float(self._candidate_value(candidate, "rating_score", 0.0) or 0.0),
                    max(0, int(self._candidate_value(candidate, "rating_count", 0) or 0)),
                    max(0, int(self._candidate_value(candidate, "source_rank", 0) or 0)),
                    candidate_key,
                ),
            )
        if filtered_by_date:
            logger.info(
                "candidate enqueue: filtered %d raw candidate(s) by publication date",
                filtered_by_date,
            )
        if max_pending_per_source is not None:
            max_pending = max(0, int(max_pending_per_source))
            if max_pending > 0:
                for source in touched_sources:
                    self.trim_discovery_candidates_for_source(
                        source_platform=source,
                        max_pending=max_pending,
                    )
        return inserted

    def trim_discovery_candidates_for_source(
        self,
        *,
        source_platform: str,
        max_pending: int,
    ) -> int:
        """Terminalize unclaimed active rows over one source's queue cap.

        Terminal history does not consume the cap. Token-owned and
        ``evaluating`` rows remain untouched; victims retain an auditable row
        with ``status='trimmed_capacity'``.
        """

        source = str(source_platform or "").strip()
        cap = max(0, int(max_pending))
        if not source or cap <= 0:
            return 0
        self._ensure_fresh_read()
        self.reject_temporally_stale_evaluated_candidates()
        active_rows = self._load_temporally_eligible_admission_waiting_rows_on(
            self.conn,
            source_platform=source,
        )
        current = len(active_rows)
        excess = current - cap
        if excess <= 0:
            return 0
        family = _pool_source_family("", source)
        victims = sorted(
            (
                row
                for row in active_rows
                if str(row["status"]) in {"pending_eval", "evaluated"}
                and row["claim_token"] is None
            ),
            key=lambda row: (
                # A due review is existing inventory being reconsidered. If
                # the source queue is over capacity, shed those retries before
                # sacrificing genuinely new supply.
                0
                if str(row["status"]) == "pending_eval"
                and str(row["eval_error"] or "").startswith("temporal_review_due:")
                else 1
                if str(row["status"]) == "pending_eval"
                else 2,
                self._sort_timestamp_score(str(row["last_seen_at"] or "")),
                int(row["id"]),
            ),
        )[:excess]
        if not victims:
            return 0
        cursor = self._execute_many_write(
            """
            UPDATE discovery_candidates
            SET status = 'trimmed_capacity',
                eval_error = ?,
                claimed_at = NULL,
                claim_token = NULL
            WHERE id = ?
              AND source_platform = ?
              AND status IN ('pending_eval', 'evaluated')
              AND claim_token IS NULL
            """,
            [(f"source_raw_ceiling:{family}", int(row["id"]), source) for row in victims],
        )
        return int(cursor.rowcount)

    def reset_stale_discovery_candidate_evaluations(
        self,
        *,
        max_age_minutes: int = 30,
    ) -> int:
        """Release evaluator claims left behind by a crashed process.

        ``max_age_minutes=0`` releases EVERY ``evaluating`` row regardless of
        age — the startup case: the evaluator lives in-process, so any claim
        that survives a restart is orphaned by definition. Rows with a NULL
        ``claimed_at`` can never age out, so both modes include them.
        Without this a restart mid-batch starves the pool forever: stuck
        rows count toward the supply target but the drain only claims
        ``pending_eval`` (field log 2026-07-05: pool_available=0 with 40
        immortal ``evaluating`` rows).
        """

        minutes = max(0, int(max_age_minutes))
        if minutes == 0:
            cursor = self._execute_write(
                """
                UPDATE discovery_candidates
                SET status = 'pending_eval',
                    claimed_at = NULL,
                    claim_token = NULL,
                    eval_error = CASE
                        WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                        THEN eval_error
                        ELSE 'orphaned evaluating claim reset'
                    END,
                    temporal_review_retry_at = CASE
                        WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                         AND (
                            COALESCE(temporal_review_retry_at, '') = ''
                            OR julianday(temporal_review_retry_at) <= julianday('now')
                         )
                        THEN datetime(
                            'now',
                            '+' || CASE
                                WHEN COALESCE(temporal_review_attempts, 0) <= 1 THEN 1
                                WHEN temporal_review_attempts = 2 THEN 2
                                WHEN temporal_review_attempts = 3 THEN 4
                                WHEN temporal_review_attempts = 4 THEN 8
                                WHEN temporal_review_attempts = 5 THEN 16
                                ELSE 24
                            END || ' hours'
                        )
                        ELSE COALESCE(temporal_review_retry_at, '')
                    END
                WHERE status = 'evaluating'
                """
            )
            return int(cursor.rowcount)
        cursor = self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = 'pending_eval',
                claimed_at = NULL,
                claim_token = NULL,
                eval_error = CASE
                    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                    THEN eval_error
                    ELSE 'stale evaluating claim reset'
                END,
                temporal_review_retry_at = CASE
                    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                     AND (
                        COALESCE(temporal_review_retry_at, '') = ''
                        OR julianday(temporal_review_retry_at) <= julianday('now')
                     )
                    THEN datetime(
                        'now',
                        '+' || CASE
                            WHEN COALESCE(temporal_review_attempts, 0) <= 1 THEN 1
                            WHEN temporal_review_attempts = 2 THEN 2
                            WHEN temporal_review_attempts = 3 THEN 4
                            WHEN temporal_review_attempts = 4 THEN 8
                            WHEN temporal_review_attempts = 5 THEN 16
                            ELSE 24
                        END || ' hours'
                    )
                    ELSE COALESCE(temporal_review_retry_at, '')
                END
            WHERE status = 'evaluating'
              AND (claimed_at IS NULL OR claimed_at < datetime('now', ?))
            """,
            (f"-{minutes} minutes",),
        )
        return int(cursor.rowcount)

    def claim_discovery_candidates_for_eval(
        self,
        *,
        limit: int,
        claim_token: str | None = None,
        preferred_source_platforms: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Claim a mixed-source batch of pending candidates for evaluation.

        Pool-share fairness (spec 2026-07-20, Phase 8 / D9): when
        ``preferred_source_platforms`` is given (under-share sources), those
        rows are pulled into the peek window ahead of the FIFO order AND drained
        first in the round-robin, so an over-supplied backlog cannot burn the
        evaluator on rows that share-aware admission won't even seat. Omitting it
        keeps the legacy round-robin over the FIFO window byte-for-byte.
        """

        claim_limit = max(0, int(limit))
        if claim_limit <= 0:
            return []
        self._ensure_fresh_read()
        platforms = [
            str(platform).strip()
            for platform in (preferred_source_platforms or [])
            if str(platform).strip()
        ]
        window_limit = max(claim_limit * 4, claim_limit)
        # Peek a bounded window and round-robin in Python so one noisy source
        # cannot monopolize a mixed evaluator batch.
        if platforms:
            placeholders = ", ".join("?" for _ in platforms)
            order_prefix = f"CASE WHEN source_platform IN ({placeholders}) THEN 0 ELSE 1 END, "
            window_params: tuple[Any, ...] = (*platforms, window_limit)
        else:
            order_prefix = ""
            window_params = (window_limit,)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM discovery_candidates
            WHERE status = 'pending_eval'
              AND (
                    COALESCE(temporal_review_retry_at, '') = ''
                    OR julianday(temporal_review_retry_at) <= julianday('now')
              )
            ORDER BY {order_prefix}last_seen_at ASC, id ASC
            LIMIT ?
            """,
            window_params,
        )
        pending = [dict(row) for row in cursor.fetchall()]
        if not pending:
            return []

        source_order: list[str] = []
        by_source: dict[str, list[dict[str, Any]]] = {}
        for row in pending:
            source = str(row.get("source_platform") or "unknown")
            if source not in by_source:
                source_order.append(source)
                by_source[source] = []
            by_source[source].append(row)

        # Two-tier round-robin: drain preferred (under-share) sources first, then
        # the rest. With no preferred list this is a single tier == legacy order.
        preferred_set = set(platforms)
        tiers = (
            [source for source in source_order if source in preferred_set],
            [source for source in source_order if source not in preferred_set],
        )
        selected: list[dict[str, Any]] = []
        for tier in tiers:
            while len(selected) < claim_limit:
                added = False
                for source in tier:
                    rows = by_source[source]
                    if not rows:
                        continue
                    selected.append(rows.pop(0))
                    added = True
                    if len(selected) >= claim_limit:
                        break
                if not added:
                    break
            if len(selected) >= claim_limit:
                break

        ids = [int(row["id"]) for row in selected]
        placeholders = ", ".join("?" for _ in ids)
        token = str(claim_token or secrets.token_hex(16))
        self._execute_write(
            f"""
            UPDATE discovery_candidates
            SET status = 'evaluating',
                claimed_at = CURRENT_TIMESTAMP,
                claim_token = ?,
                eval_error = CASE
                    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                    THEN eval_error
                    ELSE ''
                END,
                temporal_review_retry_at = CASE
                    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                    THEN datetime(
                        'now',
                        '+' || CASE
                            WHEN COALESCE(temporal_review_attempts, 0) <= 0 THEN 1
                            WHEN temporal_review_attempts = 1 THEN 2
                            WHEN temporal_review_attempts = 2 THEN 4
                            WHEN temporal_review_attempts = 3 THEN 8
                            WHEN temporal_review_attempts = 4 THEN 16
                            ELSE 24
                        END || ' hours'
                    )
                    ELSE COALESCE(temporal_review_retry_at, '')
                END,
                temporal_review_attempts = CASE
                    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                    THEN MIN(COALESCE(temporal_review_attempts, 0) + 1, 6)
                    ELSE COALESCE(temporal_review_attempts, 0)
                END
            WHERE id IN ({placeholders})
              AND status = 'pending_eval'
              AND (
                    COALESCE(temporal_review_retry_at, '') = ''
                    OR julianday(temporal_review_retry_at) <= julianday('now')
              )
            """,
            (token, *ids),
        )
        claimed_rows = self.conn.execute(
            f"""
            SELECT *
            FROM discovery_candidates
            WHERE id IN ({placeholders})
              AND status = 'evaluating'
              AND claim_token = ?
            """,
            (*ids, token),
        ).fetchall()
        claimed_by_id = {int(row["id"]): dict(row) for row in claimed_rows}
        return [
            claimed_by_id[candidate_id] for candidate_id in ids if candidate_id in claimed_by_id
        ]

    def get_evaluated_discovery_candidates_for_admission(
        self,
        *,
        limit: int,
        preferred_source_platforms: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return evaluated candidates still waiting for content-cache admission.

        Pool-share fairness (spec 2026-07-20, Phase 2): when
        ``preferred_source_platforms`` is given, under-share sources sort ahead
        of the FIFO order so a fixed admission window is not monopolized by an
        over-supplied source's backlog. Omitting it keeps the legacy
        ``evaluated_at ASC`` ordering byte-for-byte (invariant 5). Temporal
        eligibility is applied before ``limit`` so an expired backlog cannot
        hide later admissible rows.
        """

        admission_limit = max(0, int(limit))
        if admission_limit <= 0:
            return []
        self._ensure_fresh_read()
        platforms = [
            str(platform).strip()
            for platform in (preferred_source_platforms or [])
            if str(platform).strip()
        ]
        if platforms:
            placeholders = ", ".join("?" for _ in platforms)
            order_prefix = f"CASE WHEN source_platform IN ({placeholders}) THEN 0 ELSE 1 END, "
            params: tuple[Any, ...] = tuple(platforms)
        else:
            order_prefix = ""
            params = ()
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM discovery_candidates
            WHERE status = 'evaluated'
            ORDER BY {order_prefix}evaluated_at ASC, last_seen_at ASC, id ASC
            """,
            params,
        )
        temporal_now = datetime.now(UTC)
        eligible: list[dict[str, Any]] = []
        for row in cursor:
            item = dict(row)
            if not self._temporal_eligibility_for_row(item, now=temporal_now):
                continue
            eligible.append(item)
            if len(eligible) >= admission_limit:
                break
        return eligible

    def get_discovery_candidates_by_ids(
        self,
        candidate_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        """Return current durable candidate rows in caller-supplied order."""

        ids = list(dict.fromkeys(int(candidate_id) for candidate_id in candidate_ids))
        ids = [candidate_id for candidate_id in ids if candidate_id > 0]
        if not ids:
            return []
        self._ensure_fresh_read()
        rows_by_id: dict[int, dict[str, Any]] = {}
        for offset in range(0, len(ids), 900):
            chunk = ids[offset : offset + 900]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT * FROM discovery_candidates WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            rows_by_id.update((int(row["id"]), dict(row)) for row in rows)
        return [rows_by_id[candidate_id] for candidate_id in ids if candidate_id in rows_by_id]

    def update_discovery_candidate_evaluations(
        self,
        evaluations: Sequence[Mapping[str, Any]],
    ) -> int:
        """Persist evaluator output back onto claimed candidate rows."""

        updated = 0
        for evaluation in evaluations:
            candidate_id = int(evaluation.get("candidate_id") or evaluation.get("id") or 0)
            if candidate_id <= 0:
                continue
            if self._persist_discovery_candidate_evaluation(
                candidate_id=candidate_id,
                evaluation=evaluation,
                claim_token=None,
            ):
                updated += 1
        return updated

    def _persist_discovery_candidate_evaluation(
        self,
        *,
        candidate_id: int,
        evaluation: Mapping[str, Any],
        claim_token: str | None,
    ) -> bool:
        """Merge and persist one evaluation under the candidate writer lock.

        A re-review may return malformed or explicitly neutral temporal output.
        Such output must not erase older classified evidence and release a row
        whose review is still due.  The durable prompt-visible fields and the
        old evidence group are therefore read only after ``BEGIN IMMEDIATE``;
        the same transaction grounds the incoming evidence, selects the group
        to retain, derives its disposition, and performs the ownership CAS.
        """

        connection = self.conn
        token_clause = " AND claim_token = ?" if claim_token is not None else ""
        ownership_params: tuple[Any, ...] = (
            (candidate_id, claim_token) if claim_token is not None else (candidate_id,)
        )
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                if connection.in_transaction:
                    # Match the other prepared-write sinks: legacy callers may
                    # have staged work directly on ``conn`` before completing
                    # a claim, so upgrade that transaction without nesting.
                    connection.execute("UPDATE discovery_candidates SET id = id WHERE 0")
                else:
                    connection.execute("BEGIN IMMEDIATE")
                try:
                    durable_row = connection.execute(
                        f"""
                        SELECT id, published_at, published_label, title, description,
                               body_text, temporal_class, temporal_confidence,
                               temporal_reason, temporal_policy_version,
                               temporal_validity_mode, temporal_valid_until,
                               temporal_scope, temporal_state, temporal_evidence,
                               temporal_next_review_at, temporal_evaluated_at,
                               temporal_evidence_complete, eval_error,
                               temporal_review_attempts, temporal_review_retry_at
                        FROM discovery_candidates
                        WHERE id = ?
                          AND status = 'evaluating'
                          {token_clause}
                        LIMIT 1
                        """,
                        ownership_params,
                    ).fetchone()
                    if durable_row is None:
                        connection.commit()
                        return False

                    durable = dict(durable_row)
                    candidate_content_text = {
                        "title": durable.get("title", ""),
                        # Candidate evaluation uses the batch prompt, whose
                        # description projection is capped at 400 characters.
                        # The storage sink must not ground against text the
                        # model never saw.
                        "description": str(durable.get("description", "") or "")[:400],
                        "body_text": durable.get("body_text", ""),
                        "published_label": durable.get("published_label", ""),
                    }
                    incoming_grounding_downgraded = _temporal_grounding_was_downgraded(
                        evaluation,
                        content_text=candidate_content_text,
                    )
                    incoming_temporal, incoming_lifecycle = (
                        _normalize_temporal_evidence_group_for_storage(
                            evaluation,
                            content_text=candidate_content_text,
                        )
                    )
                    existing_temporal, existing_lifecycle = (
                        _normalize_temporal_evidence_group_for_storage(durable)
                    )

                    existing_is_classified = existing_temporal[0] != "unknown"
                    incoming_can_replace = (
                        incoming_temporal[3] == TEMPORAL_POLICY_VERSION
                        and incoming_temporal[0] != "unknown"
                        and bool(incoming_lifecycle[7])
                        and _temporal_evidence_is_authoritative_replacement(
                            incoming_temporal,
                            incoming_lifecycle,
                        )
                        # A syntactically complete terminal claim can be
                        # weakened by grounding (for example, its quoted
                        # evidence is absent or contradicts "expired"). It is
                        # useful as freshness-only evidence for a new row, but
                        # must never erase an older, grounded classification.
                        and not (existing_is_classified and incoming_grounding_downgraded)
                    )
                    if incoming_can_replace or not existing_is_classified:
                        temporal = incoming_temporal
                        lifecycle = incoming_lifecycle
                    else:
                        # Preserve both complete v2 evidence and classified v1
                        # evidence.  In particular, a malformed re-review does
                        # not reset an already-due row to temporal eligibility.
                        temporal = existing_temporal
                        lifecycle = existing_lifecycle

                    decision_now = datetime.now(UTC)
                    temporal_decision = evaluate_temporal_eligibility(
                        temporal_class=temporal[0],
                        temporal_confidence=temporal[1],
                        published_at=durable.get("published_at", ""),
                        temporal_validity_mode=lifecycle[0],
                        temporal_valid_until=lifecycle[1],
                        temporal_scope=lifecycle[2],
                        temporal_state=lifecycle[3],
                        temporal_evidence=lifecycle[4],
                        temporal_next_review_at=lifecycle[5],
                        temporal_evaluated_at=lifecycle[6],
                        temporal_policy_version=temporal[3],
                        evidence_complete=bool(lifecycle[7]),
                        now=decision_now,
                    )
                    requested_status = str(evaluation.get("status") or "evaluated")
                    disposition = _temporal_decision_disposition(temporal_decision)
                    stored_status = requested_status
                    stored_error = str(evaluation.get("eval_error") or "")
                    review_attempts = 0
                    review_retry_at = ""
                    if requested_status in {"evaluated", "rejected_temporal_stale"}:
                        if disposition == "expired":
                            stored_status = "rejected_temporal_stale"
                            stored_error = temporal_decision.reason
                        elif disposition == "review_due":
                            stored_status = "pending_eval"
                            stored_error = temporal_decision.reason
                            review_attempts = max(
                                0,
                                int(durable.get("temporal_review_attempts", 0) or 0),
                            )
                            review_retry_at = str(durable.get("temporal_review_retry_at", "") or "")
                            durable_has_review_marker = str(
                                durable.get("eval_error", "") or ""
                            ).startswith("temporal_review_due:")
                            retry_clock = _parse_temporal_storage_clock(review_retry_at)
                            if not durable_has_review_marker:
                                retry_hours = min(24, 2 ** min(review_attempts, 5))
                                review_attempts = min(review_attempts + 1, 6)
                                review_retry_at = (
                                    (decision_now + timedelta(hours=retry_hours))
                                    .astimezone(UTC)
                                    .isoformat()
                                    .replace("+00:00", "Z")
                                )
                            elif retry_clock is None or retry_clock <= decision_now:
                                review_retry_at = _temporal_review_retry_text(
                                    now=decision_now,
                                    attempts=max(1, review_attempts),
                                )
                        else:
                            # Storage-side grounding is authoritative.  A
                            # caller's stale status cannot survive when its
                            # evidence normalizes to an eligible group.
                            stored_status = "evaluated"
                            if requested_status == "rejected_temporal_stale":
                                stored_error = ""

                    cursor = connection.execute(
                        f"""
                        UPDATE discovery_candidates
                        SET status = ?,
                            topic_key = ?,
                            topic_group = ?,
                            style_key = ?,
                            franchise_key = ?,
                            relevance_score = ?,
                            relevance_reason = ?,
                            temporal_class = ?,
                            temporal_confidence = ?,
                            temporal_reason = ?,
                            temporal_policy_version = ?,
                            temporal_validity_mode = ?,
                            temporal_valid_until = ?,
                            temporal_scope = ?,
                            temporal_state = ?,
                            temporal_evidence = ?,
                            temporal_next_review_at = ?,
                            temporal_evaluated_at = ?,
                            temporal_evidence_complete = ?,
                            temporal_review_attempts = ?,
                            temporal_review_retry_at = ?,
                            pool_expression = ?,
                            pool_topic_label = ?,
                            eval_error = ?,
                            eval_attempts = 0,
                            batch_eval_attempts = 0,
                            evaluated_at = CURRENT_TIMESTAMP,
                            claimed_at = NULL,
                            claim_token = NULL
                        WHERE id = ?
                          AND status = 'evaluating'
                          {token_clause}
                        """,
                        (
                            stored_status,
                            str(evaluation.get("topic_key") or ""),
                            str(evaluation.get("topic_group") or ""),
                            _normalize_style_key_for_storage(evaluation.get("style_key")),
                            str(evaluation.get("franchise_key") or ""),
                            float(
                                evaluation.get("relevance_score") or evaluation.get("score") or 0.0
                            ),
                            str(
                                evaluation.get("relevance_reason") or evaluation.get("reason") or ""
                            ),
                            temporal[0],
                            temporal[1],
                            temporal[2],
                            temporal[3],
                            *lifecycle,
                            review_attempts,
                            review_retry_at,
                            str(evaluation.get("pool_expression") or ""),
                            str(evaluation.get("pool_topic_label") or ""),
                            stored_error,
                            *ownership_params,
                        ),
                    )
                    connection.commit()
                    return cursor.rowcount > 0
                except Exception:
                    connection.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                self._rollback_failed_write(connection)
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite candidate evaluation write locked, retrying (%s attempts left)",
                    attempts,
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def persist_claimed_discovery_candidate_evaluations(
        self,
        evaluations: Sequence[Mapping[str, Any]],
        *,
        claim_token: str,
    ) -> set[int]:
        """Persist outputs only while the caller still owns the claim token."""

        updated_ids: set[int] = set()
        token = str(claim_token)
        for evaluation in evaluations:
            candidate_id = int(evaluation.get("candidate_id") or evaluation.get("id") or 0)
            if candidate_id <= 0:
                continue
            if self._persist_discovery_candidate_evaluation(
                candidate_id=candidate_id,
                evaluation=evaluation,
                claim_token=token,
            ):
                updated_ids.add(candidate_id)
        return updated_ids

    def reset_claimed_discovery_candidates_to_pending(
        self,
        candidate_ids: Sequence[int],
        *,
        claim_token: str,
        reason: str = "",
        max_attempts: int = 5,
        max_batch_attempts: int = 50,
        increment_attempts: bool = True,
    ) -> int:
        """Release candidates only while the caller still owns their claim."""

        ids = [int(candidate_id) for candidate_id in candidate_ids if int(candidate_id) > 0]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        token = str(claim_token)
        if not increment_attempts:
            batch_attempts_limit = max(1, int(max_batch_attempts))
            cursor = self._execute_write(
                f"""
                UPDATE discovery_candidates
                SET batch_eval_attempts = batch_eval_attempts + 1,
                    status = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN 'failed_eval'
                        ELSE 'pending_eval'
                    END,
                    claimed_at = NULL,
                    claim_token = NULL,
                    eval_error = CASE
                        WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                        THEN eval_error
                        ELSE ?
                    END,
                    {_TEMPORAL_REVIEW_RETRY_REFRESH_ASSIGNMENT_SQL},
                    evaluated_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                        ELSE evaluated_at
                    END,
                    last_seen_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN last_seen_at
                        ELSE CURRENT_TIMESTAMP
                    END
                WHERE id IN ({placeholders})
                  AND status = 'evaluating'
                  AND claim_token = ?
                """,
                (
                    batch_attempts_limit,
                    str(reason),
                    batch_attempts_limit,
                    batch_attempts_limit,
                    *ids,
                    token,
                ),
            )
            return int(cursor.rowcount)

        attempts_limit = max(1, int(max_attempts))
        cursor = self._execute_write(
            f"""
            UPDATE discovery_candidates
            SET eval_attempts = eval_attempts + 1,
                status = CASE
                    WHEN eval_attempts + 1 >= ? THEN 'failed_eval'
                    ELSE 'pending_eval'
                END,
                claimed_at = NULL,
                claim_token = NULL,
                eval_error = CASE
                    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                    THEN eval_error
                    ELSE ?
                END,
                {_TEMPORAL_REVIEW_RETRY_REFRESH_ASSIGNMENT_SQL},
                evaluated_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                    ELSE evaluated_at
                END,
                last_seen_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN last_seen_at
                    ELSE CURRENT_TIMESTAMP
                END
            WHERE id IN ({placeholders})
              AND status = 'evaluating'
              AND claim_token = ?
            """,
            (attempts_limit, str(reason), attempts_limit, attempts_limit, *ids, token),
        )
        return int(cursor.rowcount)

    def reset_discovery_candidates_to_pending(
        self,
        candidate_ids: Sequence[int],
        *,
        reason: str = "",
        max_attempts: int = 5,
        max_batch_attempts: int = 50,
        increment_attempts: bool = True,
    ) -> int:
        """Release claimed candidates after a transient evaluator failure."""

        ids = [int(candidate_id) for candidate_id in candidate_ids if int(candidate_id) > 0]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        if not increment_attempts:
            batch_attempts_limit = max(1, int(max_batch_attempts))
            cursor = self._execute_write(
                f"""
                UPDATE discovery_candidates
                SET batch_eval_attempts = batch_eval_attempts + 1,
                    status = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN 'failed_eval'
                        ELSE 'pending_eval'
                    END,
                    claimed_at = NULL,
                    claim_token = NULL,
                    eval_error = CASE
                        WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                        THEN eval_error
                        ELSE ?
                    END,
                    {_TEMPORAL_REVIEW_RETRY_REFRESH_ASSIGNMENT_SQL},
                    evaluated_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                        ELSE evaluated_at
                    END,
                    last_seen_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN last_seen_at
                        ELSE CURRENT_TIMESTAMP
                    END
                WHERE id IN ({placeholders})
                  AND status = 'evaluating'
                """,
                (
                    batch_attempts_limit,
                    str(reason),
                    batch_attempts_limit,
                    batch_attempts_limit,
                    *ids,
                ),
            )
            return int(cursor.rowcount)

        attempts_limit = max(1, int(max_attempts))
        cursor = self._execute_write(
            f"""
            UPDATE discovery_candidates
            SET eval_attempts = eval_attempts + 1,
                status = CASE
                    WHEN eval_attempts + 1 >= ? THEN 'failed_eval'
                    ELSE 'pending_eval'
                END,
                claimed_at = NULL,
                claim_token = NULL,
                eval_error = CASE
                    WHEN COALESCE(eval_error, '') LIKE 'temporal_review_due:%'
                    THEN eval_error
                    ELSE ?
                END,
                {_TEMPORAL_REVIEW_RETRY_REFRESH_ASSIGNMENT_SQL},
                evaluated_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                    ELSE evaluated_at
                END,
                last_seen_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN last_seen_at
                    ELSE CURRENT_TIMESTAMP
                END
            WHERE id IN ({placeholders})
              AND status = 'evaluating'
            """,
            (attempts_limit, str(reason), attempts_limit, attempts_limit, *ids),
        )
        return int(cursor.rowcount)

    def mark_discovery_candidate_cached(self, candidate_id: int) -> None:
        """Mark an evaluated candidate as successfully inserted into content_cache."""

        self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = 'cached',
                cached_at = CURRENT_TIMESTAMP,
                eval_error = '',
                eval_attempts = 0,
                batch_eval_attempts = 0,
                temporal_review_attempts = 0,
                temporal_review_retry_at = ''
                , claimed_at = NULL
                , claim_token = NULL
            WHERE id = ?
              AND status IN ('evaluating', 'evaluated')
            """,
            (int(candidate_id),),
        )

    def reject_discovery_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        reason: str = "",
    ) -> None:
        """Mark a candidate as rejected before it enters content_cache."""

        self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = ?,
                eval_error = ?,
                evaluated_at = COALESCE(evaluated_at, CURRENT_TIMESTAMP),
                temporal_review_attempts = 0,
                temporal_review_retry_at = '',
                claimed_at = NULL,
                claim_token = NULL
            WHERE id = ?
              AND status IN ('evaluating', 'evaluated')
            """,
            (status, reason, int(candidate_id)),
        )

    def requeue_discovery_candidate_for_temporal_review(
        self,
        candidate_id: int,
        reason: str = "",
    ) -> int:
        """CAS one owned/waiting candidate back to the evaluator for review."""

        cursor = self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = 'pending_eval',
                eval_error = ?,
                temporal_review_attempts = MAX(
                    COALESCE(temporal_review_attempts, 0),
                    1
                ),
                temporal_review_retry_at = CASE
                    WHEN COALESCE(temporal_review_retry_at, '') = ''
                      OR julianday(temporal_review_retry_at) <= julianday('now')
                    THEN datetime(
                        'now',
                        '+' || CASE
                            WHEN COALESCE(temporal_review_attempts, 0) <= 1 THEN 1
                            WHEN temporal_review_attempts = 2 THEN 2
                            WHEN temporal_review_attempts = 3 THEN 4
                            WHEN temporal_review_attempts = 4 THEN 8
                            WHEN temporal_review_attempts = 5 THEN 16
                            ELSE 24
                        END || ' hours'
                    )
                    ELSE temporal_review_retry_at
                END,
                claimed_at = NULL,
                claim_token = NULL,
                last_seen_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('evaluating', 'evaluated')
            """,
            (str(reason), int(candidate_id)),
        )
        return int(cursor.rowcount)

    def count_discovery_candidates_by_status(self) -> dict[str, int]:
        """Return lifecycle totals plus the currently claimable pending count."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM discovery_candidates
            GROUP BY status
            ORDER BY status ASC
            """
        )
        counts = {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}
        ready_row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM discovery_candidates
            WHERE status = 'pending_eval'
              AND (
                    COALESCE(temporal_review_retry_at, '') = ''
                    OR julianday(temporal_review_retry_at) <= julianday('now')
              )
            """
        ).fetchone()
        counts["pending_eval_ready"] = int(ready_row["count"] if ready_row is not None else 0)
        return counts

    def get_existing_discovery_candidate_keys(self, candidate_keys: Sequence[str]) -> set[str]:
        """Return candidate keys already present in the raw evaluation queue."""

        clean = _unique_clean_strings(candidate_keys)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 900):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT candidate_key
                FROM discovery_candidates
                WHERE candidate_key IN ({placeholders})
                """,
                chunk,
            )
            existing.update(str(row["candidate_key"]) for row in cursor.fetchall())
        return existing

    def get_existing_content_cache_ids(self, content_ids: Sequence[str]) -> set[str]:
        """Return BVID/content ids that already exist in the evaluated content cache."""

        clean = _unique_clean_strings(content_ids)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 450):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT bvid, content_id
                FROM content_cache
                WHERE bvid IN ({placeholders})
                   OR content_id IN ({placeholders})
                """,
                [*chunk, *chunk],
            )
            for row in cursor.fetchall():
                bvid = str(row["bvid"] or "").strip()
                content_id = str(row["content_id"] or "").strip()
                if bvid:
                    existing.add(bvid)
                if content_id:
                    existing.add(content_id)
        return existing

    def get_existing_content_cache_item_keys(self, item_keys: Sequence[str]) -> set[str]:
        """Return canonical identities already present in the evaluated content cache."""
        clean = _unique_clean_strings(item_keys)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 900):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"SELECT item_key FROM content_cache WHERE item_key IN ({placeholders})",
                chunk,
            )
            existing.update(str(row["item_key"]) for row in cursor.fetchall())
        return existing

    def count_discovery_candidates_by_source_status(self) -> dict[str, dict[str, int]]:
        """Return candidate queue counts grouped by source and lifecycle status."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT source_platform, status, COUNT(*) AS count
            FROM discovery_candidates
            GROUP BY source_platform, status
            ORDER BY source_platform ASC, status ASC
            """
        )
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            source = str(row["source_platform"] or "unknown")
            status = str(row["status"])
            counts.setdefault(source, {})[status] = int(row["count"])
        return counts

    def _load_temporally_eligible_admission_waiting_rows_on(
        self,
        conn: sqlite3.Connection,
        *,
        source_platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return non-terminal discovery rows that still count as supply.

        ``pending_eval`` and ``evaluating`` have not received an Agent class,
        so they remain fail-open. ``evaluated`` rows can cross a temporal TTL
        while waiting for pool headroom and are rechecked here before they
        influence readiness, raw ceilings, or source-share rebalancing.
        """

        source = str(source_platform or "").strip()
        source_clause = " AND source_platform = ?" if source else ""
        cursor = conn.execute(
            f"""
            SELECT id, status, source_platform, source_strategy,
                   relevance_score, last_seen_at, claim_token, eval_error,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete,
                   temporal_review_attempts, temporal_review_retry_at
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
              AND (
                    status != 'pending_eval'
                    OR COALESCE(temporal_review_retry_at, '') = ''
                    OR julianday(temporal_review_retry_at) <= julianday('now')
              )
            {source_clause}
            """,
            (source,) if source else (),
        )
        temporal_now = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            item = dict(row)
            if str(item.get("status", "")) == "evaluated" and not (
                self._temporal_eligibility_for_row(item, now=temporal_now)
            ):
                continue
            rows.append(item)
        return rows

    def reject_temporally_stale_evaluated_candidates(
        self,
        *,
        limit: int = 500,
        now: datetime | None = None,
    ) -> int:
        """Terminalize candidates that expired while waiting for admission.

        Acquire the writer lock before the read so rediscovery cannot refresh
        a candidate between its temporal decision and terminal status update.
        """

        max_rows = max(0, int(limit))
        if max_rows == 0:
            return 0
        explicit_now = now
        if explicit_now is not None and explicit_now.tzinfo is None:
            explicit_now = explicit_now.replace(tzinfo=UTC)
        self._ensure_fresh_read()
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            connection = self.conn
            try:
                cursor = connection.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    rows = cursor.execute(
                        """
                        SELECT id, published_at, temporal_class, temporal_confidence,
                               temporal_policy_version, temporal_validity_mode,
                               temporal_valid_until, temporal_scope, temporal_state,
                               temporal_evidence,
                               temporal_next_review_at, temporal_evaluated_at,
                               temporal_evidence_complete
                        FROM discovery_candidates
                        WHERE status = 'evaluated'
                          AND claim_token IS NULL
                        ORDER BY evaluated_at ASC, last_seen_at ASC, id ASC
                        """
                    )
                    effective_now = explicit_now or datetime.now(UTC)
                    stale: list[tuple[str, int]] = []
                    review_due: list[tuple[str, int]] = []
                    for row in rows:
                        decision = self._temporal_decision_for_row(
                            dict(row),
                            now=effective_now,
                        )
                        disposition = _temporal_decision_disposition(decision)
                        if disposition == "expired":
                            stale.append((decision.reason, int(row["id"])))
                        elif disposition == "review_due":
                            review_due.append((decision.reason, int(row["id"])))
                        if len(stale) + len(review_due) >= max_rows:
                            break
                    if stale:
                        result = cursor.executemany(
                            """
                            UPDATE discovery_candidates
                            SET status = 'rejected_temporal_stale',
                                eval_error = ?,
                                evaluated_at = COALESCE(evaluated_at, CURRENT_TIMESTAMP),
                                claimed_at = NULL,
                                claim_token = NULL
                            WHERE id = ?
                              AND status = 'evaluated'
                              AND claim_token IS NULL
                            """,
                            stale,
                        )
                        updated = int(result.rowcount)
                    else:
                        updated = 0
                    if review_due:
                        cursor.executemany(
                            """
                            UPDATE discovery_candidates
                            SET status = 'pending_eval',
                                eval_error = ?,
                                claimed_at = NULL,
                                claim_token = NULL,
                                last_seen_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                              AND status = 'evaluated'
                              AND claim_token IS NULL
                            """,
                            review_due,
                        )
                    connection.commit()
                    return updated
                except Exception:
                    connection.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                self._rollback_failed_write(connection)
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite temporal candidate sweep locked, retrying (%s attempts left)",
                    attempts,
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def count_discovery_pending_raw_material_by_source(self) -> dict[str, int]:
        """Return not-yet-cached raw candidate counts grouped by source."""

        self._ensure_fresh_read()
        counts: dict[str, int] = defaultdict(int)
        for row in self._load_temporally_eligible_admission_waiting_rows_on(self.conn):
            counts[str(row.get("source_platform") or "unknown")] += 1
        return dict(sorted(counts.items()))

    def _count_pending_discovery_raw_material(self) -> int:
        self._ensure_fresh_read()
        return len(self._load_temporally_eligible_admission_waiting_rows_on(self.conn))

    def get_cached_content(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get cached discovered content ordered by basic quality signals."""
        cursor = self.conn.execute(
            """
            SELECT *
            FROM content_cache
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_unrecommended_content(
        self,
        limit: int = 100,
        *,
        source_platforms: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get cached content that has not been recommended yet.

        When ``source_platforms`` is supplied, filtering happens in SQL before
        balancing and limiting. Blank legacy platform values count as
        ``bilibili`` so old Bilibili cache rows remain eligible without opening
        a cross-platform backfill path.
        """
        platform_sql = ""
        platform_params: tuple[str, ...] = ()
        if source_platforms is not None:
            normalized_platforms = tuple(
                dict.fromkeys(
                    normalize_source_platform(platform, default=_BILIBILI_SOURCE_FAMILY)
                    for platform in source_platforms
                    if str(platform or "").strip()
                )
            )
            if not normalized_platforms:
                return []
            placeholders = ",".join("?" for _ in normalized_platforms)
            platform_sql = (
                "AND COALESCE(NULLIF(LOWER(TRIM(c.source_platform)), ''), 'bilibili') "
                f"IN ({placeholders})"
            )
            platform_params = normalized_platforms
        admission_sql, admission_params = self._pool_admission_sql(
            score_expr="COALESCE(c.relevance_score, 0.0)",
            source_expr="c.source",
        )
        cursor = self.conn.execute(
            f"""
            SELECT c.*
            FROM content_cache AS c
            WHERE {admission_sql}
              AND COALESCE(c.pool_status, 'fresh') = 'fresh'
              AND COALESCE(c.feedback_type, '') != 'dislike'
              {platform_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = c.bvid
            )
            ORDER BY
                CASE c.candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                c.relevance_score DESC,
                c.last_scored_at DESC,
                c.view_count DESC,
                c.bvid ASC
            """,
            (*admission_params, *platform_params),
        )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def suppress_low_score_pool_items(self, min_score: float | None = None) -> int:
        """Suppress cached pool rows below the unified admission floor."""
        if min_score is None:
            admission_sql, admission_params = self._pool_admission_sql()
        else:
            admission_sql = "COALESCE(relevance_score, 0.0) >= ?"
            admission_params = (_normalize_admission_min_score(min_score),)
        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE NOT ({admission_sql})
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """,
            admission_params,
        )
        return int(cursor.rowcount or 0)

    def suppress_low_confidence_recommendations(self, min_score: float | None = None) -> int:
        """Mark old low-confidence recommendation rows as suppressed."""
        if min_score is None:
            admission_sql, admission_params = self._pool_admission_sql(
                score_expr="COALESCE(recommendations.confidence, 0.0)",
                source_expr=(
                    "(SELECT source FROM content_cache "
                    "WHERE content_cache.bvid = recommendations.bvid LIMIT 1)"
                ),
            )
        else:
            admission_sql = "COALESCE(recommendations.confidence, 0.0) >= ?"
            admission_params = (_normalize_admission_min_score(min_score),)
        cursor = self._execute_write(
            f"""
            UPDATE recommendations
            SET feedback_type = 'suppressed_low_score'
            WHERE NOT ({admission_sql})
              AND COALESCE(feedback_type, '') = ''
            """,
            admission_params,
        )
        return int(cursor.rowcount or 0)

    def retire_temporally_stale_pool_items(
        self,
        *,
        now: datetime | None = None,
    ) -> set[str]:
        """Atomically demote expired temporal content out of the fresh pool.

        The same pure policy guards initial admission and final serving.  This
        sweep persists the middle transition so every existing ``fresh``-only
        inventory, copy, enrichment, and delight query naturally stops seeing
        items that expired while waiting in ``content_cache``.
        """

        explicit_now = now
        if explicit_now is not None and explicit_now.tzinfo is None:
            explicit_now = explicit_now.replace(tzinfo=UTC)
        self._ensure_fresh_read()
        # The common path has no expired rows. Keep it read-only so every
        # reshuffle does not acquire a SQLite RESERVED lock merely to commit a
        # no-op. A positive preflight is only a hint; the write transaction
        # re-reads and re-evaluates all fresh rows before mutating anything.
        preflight_rows = self.conn.execute(
            """
            SELECT bvid, published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
            """
        ).fetchall()
        preflight_now = explicit_now or datetime.now(UTC)
        if not any(
            str(row["bvid"] or "")
            and not self._temporal_eligibility_for_row(
                dict(row),
                now=preflight_now,
            )
            for row in preflight_rows
        ):
            return set()

        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    rows = cursor.execute(
                        """
                        SELECT bvid, published_at, temporal_class, temporal_confidence,
                               temporal_policy_version, temporal_validity_mode,
                               temporal_valid_until, temporal_scope, temporal_state,
                               temporal_evidence,
                               temporal_next_review_at, temporal_evaluated_at,
                               temporal_evidence_complete
                        FROM content_cache
                        WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                        """
                    ).fetchall()
                    effective_now = explicit_now or datetime.now(UTC)
                    transitions: list[tuple[str, str]] = []
                    for row in rows:
                        bvid = str(row["bvid"] or "")
                        if not bvid:
                            continue
                        decision = self._temporal_decision_for_row(
                            dict(row),
                            now=effective_now,
                        )
                        disposition = _temporal_decision_disposition(decision)
                        if disposition == "expired":
                            transitions.append(("stale", bvid))
                        elif disposition == "review_due":
                            transitions.append(("temporal_review_hold", bvid))
                    if transitions:
                        cursor.executemany(
                            """
                            UPDATE content_cache
                            SET pool_status = ?
                            WHERE bvid = ?
                              AND COALESCE(pool_status, 'fresh') = 'fresh'
                            """,
                            transitions,
                        )
                    self.conn.commit()
                    if transitions:
                        logger.info(
                            "held or retired %d temporally due item(s) from the fresh pool",
                            len(transitions),
                        )
                    return {bvid for _status, bvid in transitions}
                except Exception:
                    self.conn.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                self._rollback_failed_write(self.conn)
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def _transition_temporally_due_pool_items_on(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[int, bool]:
        """Persist due fresh rows before maintenance counts or trims them."""

        max_rows = max(0, int(limit))
        if max_rows == 0:
            return 0, False
        rows = conn.execute(
            """
            SELECT bvid, published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state,
                   temporal_evidence, temporal_next_review_at,
                   temporal_evaluated_at, temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
            ORDER BY bvid ASC
            """
        ).fetchall()
        transitions: list[tuple[str, str]] = []
        has_more = False
        for row in rows:
            decision = self._temporal_decision_for_row(dict(row), now=now)
            disposition = _temporal_decision_disposition(decision)
            if disposition == "eligible":
                continue
            if len(transitions) >= max_rows:
                has_more = True
                break
            transitions.append(
                (
                    "stale" if disposition == "expired" else "temporal_review_hold",
                    str(row["bvid"]),
                )
            )
        if transitions:
            conn.executemany(
                """
                UPDATE content_cache
                SET pool_status = ?
                WHERE bvid = ?
                  AND COALESCE(pool_status, 'fresh') = 'fresh'
                """,
                transitions,
            )
        return len(transitions), has_more

    def get_pool_candidates(
        self,
        limit: int = 20,
        *,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get fresh recommendation candidates directly from the discovery pool.

        ``max_per_topic_group`` caps how many items from any single
        ``topic_group`` enter the relevance-ordered head. Without this
        cap, a 600-item pool that contains 270 distinct topic_groups still
        produces a top-50 shortlist concentrated in ~10 head groups,
        because high-relevance candidates cluster around the user's
        primary interests; long-tail groups (197 with a single item each
        in the typical pool) never reach the candidate window. Cap of 3
        lets obvious favourites keep a strong presence while opening
        room for ~40+ different groups in the candidate window. Pass
        ``max_per_topic_group=0`` to restore the legacy unrestricted
        ordering for callers that need it (e.g. health checks).

        Rows claimed by the surprise (delight) channel are excluded via the
        delight claim guard — a delight that was delivered or is
        currently queue-eligible must never be duplicated by the regular
        feed. ``count_pool_candidates`` applies the same guard so the
        "还有 N 条" display stays in sync with what serve() can load.

        Notes:
            xhs rows without ``xsec_token`` in their ``content_url`` are
            excluded. Bare xhs URLs get rejected by xhs with error 300031
            when shared outbound, so surfacing them in recommendations
            would just mint dead links. Tokens get backfilled by the
            MAIN-world sniffer as the user browses xhs; bare rows become
            eligible again once ``_backfill_xhs_tokens`` upgrades them.
        """
        self._ensure_fresh_read()
        # Over-fetch widely so the per-group filter still leaves headroom
        # for the downstream balance pass.
        fetch_limit = max(limit * 8, 80)
        viewed_content_keys = (
            self.get_recent_viewed_content_keys()
            if _viewed_content_keys is None
            else _viewed_content_keys
        )
        canonical_rows = self._load_available_pool_candidate_rows_on(
            self.conn,
            max_per_topic_group=max_per_topic_group,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=viewed_content_keys,
        )
        head_bvids = [str(row["bvid"]) for row in canonical_rows[:fetch_limit]]
        rows = self._load_content_cache_rows_by_bvid_on(self.conn, head_bvids)
        return self._balance_pool_rows(rows, limit=limit)

    @staticmethod
    def _load_content_cache_rows_by_bvid_on(
        conn: sqlite3.Connection,
        bvids: Sequence[str],
    ) -> list[dict[str, Any]]:
        """Load full cache rows while preserving a canonical bvid order."""

        ordered_bvids = [str(bvid) for bvid in bvids if str(bvid)]
        if not ordered_bvids:
            return []
        by_bvid: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(ordered_bvids), 500):
            chunk = ordered_bvids[offset : offset + 500]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = conn.execute(
                f"SELECT * FROM content_cache WHERE bvid IN ({placeholders})",
                chunk,
            )
            by_bvid.update((str(row["bvid"]), dict(row)) for row in cursor.fetchall())
        return [by_bvid[bvid] for bvid in ordered_bvids if bvid in by_bvid]

    def _pool_servable_where_clause_on(
        self,
        conn: sqlite3.Connection,
        xhs_self_nickname: str,
        *,
        pool_status: str = "fresh",
    ) -> tuple[str, tuple[Any, ...]]:
        """Shared WHERE fragment + params defining a ``serve()``-loadable row.

        Central definition of "servable right now", mirroring the gate baked
        into ``get_pool_candidates`` / ``_load_available_pool_candidate_rows``:
        fresh, not disliked, at/above the admission floor, fully classified
        (pool_expression / pool_topic_label / style_key / topic_group), xhs
        rows carrying an ``xsec_token``, not claimed by the delight channel,
        and not already recommended. Returns the fragment (no leading
        ``WHERE``, references the ``content_cache`` table) and its bind params.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_threshold = self._dynamic_delight_threshold_on(
            conn, default_threshold=_DELIGHT_CLAIM_MIN_SCORE
        )
        delight_guard_sql = _delight_claim_guard_sql()
        clause = f"""
            COALESCE(pool_status, 'fresh') = ?
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND COALESCE(style_key, '') != ''
              AND COALESCE(topic_group, '') != ''
              AND (
                source_platform != 'xiaohongshu'
                OR content_url LIKE '%xsec_token=%'
              )
              {guard_sql}
              {delight_guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
        """
        return clause, (pool_status, *admission_params, *guard_params, delight_threshold)

    def _available_platform_rows_on(
        self,
        conn: sqlite3.Connection,
        platform: str,
        *,
        limit: int | None = None,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
        _available_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Return canonical available rows belonging to one source family.

        The platform filter is applied in Python on ``_pool_source_family``
        rather than in SQL: a row's family also derives from legacy
        ``source`` strategy prefixes (``zhihu-hot``, ``xhs-extension-task``)
        when ``source_platform`` is blank, so a raw column comparison would
        silently drop stocked legacy rows and break the
        ``total == sum(by_platform)`` invariant the tab counts rely on.

        Materializing the canonical set costs a full-row scan, so callers
        that need several platforms from one read (the serve window's
        platform floor) pass ``_available_rows`` to share a single load
        instead of paying that scan once per platform.
        """
        canonical = normalize_source_platform(platform)
        if not canonical:
            return []
        rows = (
            _available_rows
            if _available_rows is not None
            else self._load_available_pool_candidate_rows_on(
                conn,
                max_per_topic_group=max_per_topic_group,
                xhs_self_nickname=xhs_self_nickname,
                _viewed_content_keys=_viewed_content_keys,
                full_rows=True,
            )
        )
        selected: list[dict[str, Any]] = []
        for row in rows:
            if _pool_source_family(row["source"], row["source_platform"]) != canonical:
                continue
            selected.append(row)
            if limit is not None and len(selected) >= limit:
                break
        return selected

    def get_pool_candidates_for_platform(
        self,
        platform: str,
        limit: int = 5,
        *,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch up to ``limit`` servable pool rows for one canonical platform.

        Two callers share this reader, so it must never diverge from the
        counted inventory:

        * the serve window's platform floor, which back-fills a stocked
          non-bilibili platform an all-bilibili relevance window would
          otherwise drop for hours;
        * platform-scoped recommendation requests from PC Web, where every
          returned item has to belong to the requested platform.

        Rows come straight out of the canonical available set backing
        ``count_pool_candidates`` / ``load_pool_platform_availability`` — same
        servability, recently-viewed, linkability, delight-claim and
        topic-window guards, same relevance ordering — so
        ``strict_platform_candidates(p) ⊆ available_candidates_for(p)`` holds
        and a tab can never advertise stock this reader refuses to serve.
        ``platform`` is canonicalized (``xhs`` → ``xiaohongshu``); an empty
        value keeps the historical bilibili default.
        """
        token = normalize_source_platform(platform) or _BILIBILI_SOURCE_FAMILY
        fetch_limit = max(0, int(limit))
        if fetch_limit <= 0:
            return []
        self._ensure_fresh_read()
        return self._available_platform_rows_on(
            self.conn,
            token,
            limit=fetch_limit,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=_viewed_content_keys,
        )

    def list_servable_pool_platforms(
        self,
        *,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
    ) -> list[str]:
        """Return the distinct platform tokens among currently-servable rows.

        Same servability gate as ``get_pool_candidates`` (via
        ``_load_available_pool_candidate_rows``, which also drops
        recently-viewed and non-linkable rows). Used by the serve window's
        platform floor to detect stocked platforms a single relevance-ordered
        window can silently drop. Tokens are lowercased and default to
        ``"bilibili"`` when ``source_platform`` is blank, matching
        ``RecommendationEngine._platform_token``.
        """
        rows = self._load_available_pool_candidate_rows(
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=_viewed_content_keys,
        )
        platforms: set[str] = set()
        for row in rows:
            token = str(row.get("source_platform", "") or "").strip().lower() or "bilibili"
            platforms.add(token)
        return sorted(platforms)

    def count_pool_candidates(
        self,
        *,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
    ) -> int:
        """Return how many fresh candidates are immediately available for reshuffle.

        v0.3.57+: matches ``get_pool_candidates`` precompute gate — rows
        without ``pool_expression`` / ``pool_topic_label`` are excluded so
        the popup's "还有 N 条" never overstates what serve() can actually
        return.

        v0.3.66+: also requires ``style_key`` / ``topic_group`` — content
        must be classified before it can be served, regardless of source
        platform.

        v0.3.91+: applies the same ``max_per_topic_group`` window as
        ``get_pool_candidates`` so concentrated topic groups don't inflate
        the displayed count beyond what ``serve()`` can actually load.
        """
        return len(
            self._load_available_pool_candidate_rows(
                max_per_topic_group=max_per_topic_group,
                xhs_self_nickname=xhs_self_nickname,
                _viewed_content_keys=_viewed_content_keys,
            )
        )

    def _load_available_pool_candidate_rows(
        self,
        *,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Load rows counted by the frontend-visible pool availability gate."""
        self._ensure_fresh_read()
        return self._load_available_pool_candidate_rows_on(
            self.conn,
            max_per_topic_group=max_per_topic_group,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=_viewed_content_keys,
        )

    def _load_available_pool_candidate_rows_on(
        self,
        conn: sqlite3.Connection,
        *,
        max_per_topic_group: int = 3,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
        full_rows: bool = False,
    ) -> list[dict[str, Any]]:
        """Load rows counted by the frontend-visible pool availability gate.

        Applies the delight claim guard like ``get_pool_candidates`` so
        the availability count never includes surprise-channel rows serve()
        would refuse to load.

        ``full_rows=True`` widens the projection to whole ``content_cache``
        rows without touching the gate or ordering, so platform-scoped serve
        reads can hand complete candidates to the recommendation path while
        still coming from the exact set the counts report.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_threshold = self._dynamic_delight_threshold_on(
            conn, default_threshold=_DELIGHT_CLAIM_MIN_SCORE
        )
        delight_guard_sql = _delight_claim_guard_sql()
        projection = (
            "*"
            if full_rows
            else (
                "bvid, content_id, source, source_platform, content_url, topic_group, "
                "candidate_tier, relevance_score, last_scored_at, view_count, "
                "published_at, temporal_class, temporal_confidence, "
                "temporal_policy_version, temporal_validity_mode, "
                "temporal_valid_until, temporal_scope, temporal_state, temporal_evidence, "
                "temporal_next_review_at, temporal_evaluated_at, "
                "temporal_evidence_complete"
            )
        )
        cursor = conn.execute(
            f"""
            SELECT {projection}
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND COALESCE(style_key, '') != ''
              AND COALESCE(topic_group, '') != ''
              AND (
                source_platform != 'xiaohongshu'
                OR content_url LIKE '%xsec_token=%'
              )
              {guard_sql}
              {delight_guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            """,
            (*admission_params, *guard_params, delight_threshold),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        viewed_content_keys = (
            self._recent_viewed_content_keys_on(conn)
            if _viewed_content_keys is None
            else _viewed_content_keys
        )
        filtered = self._filter_available_pool_candidate_rows(
            rows,
            viewed_content_keys=viewed_content_keys,
        )
        return self._apply_pool_topic_window(
            filtered,
            max_per_topic_group=max_per_topic_group,
        )

    def _filter_available_pool_candidate_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        viewed_content_keys: set[str],
    ) -> list[dict[str, Any]]:
        """Apply the Python half of the canonical availability gate."""

        filtered: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for row in rows:
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if not Database._temporal_eligibility_for_row(row_dict, now=now):
                continue
            if not self._publication_date_row_is_eligible(row_dict):
                continue
            if Database._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row_dict.get("source"),
                row_dict.get("source_platform"),
                row_dict.get("content_url"),
            ):
                continue
            filtered.append(row_dict)
        return filtered

    @staticmethod
    def _temporal_eligibility_for_row(
        row: Mapping[str, Any],
        *,
        now: datetime,
    ) -> bool:
        """Return whether one stored row remains temporally recommendable."""

        return Database._temporal_decision_for_row(row, now=now).eligible

    @staticmethod
    def _temporal_decision_for_row(
        row: Mapping[str, Any],
        *,
        now: datetime,
    ) -> TemporalEligibilityDecision:
        """Evaluate the complete stored temporal evidence group."""

        return evaluate_temporal_eligibility(
            temporal_class=row.get("temporal_class", "unknown"),
            temporal_confidence=row.get("temporal_confidence", 0.0),
            published_at=row.get("published_at", ""),
            temporal_validity_mode=row.get("temporal_validity_mode", "none"),
            temporal_valid_until=row.get("temporal_valid_until", ""),
            temporal_scope=row.get("temporal_scope", "none"),
            temporal_state=row.get("temporal_state", "unknown"),
            temporal_evidence=row.get("temporal_evidence", ""),
            temporal_next_review_at=row.get("temporal_next_review_at", ""),
            temporal_evaluated_at=row.get("temporal_evaluated_at", ""),
            evidence_complete=is_complete_temporal_evidence_marker(
                row.get("temporal_evidence_complete")
            ),
            temporal_policy_version=row.get("temporal_policy_version", "v1"),
            now=now,
        )

    @staticmethod
    def _apply_pool_topic_window(
        rows: Sequence[Mapping[str, Any]],
        *,
        max_per_topic_group: int,
    ) -> list[dict[str, Any]]:
        """Apply topic rank after canonical seen/linkability filtering.

        Topic-local ranking intentionally excludes ``candidate_tier`` while
        the returned global order retains it, matching the former SQL window
        function's two distinct orderings.
        """

        topic_cap = max(0, int(max_per_topic_group))
        materialized = [dict(row) for row in rows]
        if topic_cap <= 0:
            return materialized

        rows_by_topic: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in materialized:
            rows_by_topic[str(row.get("topic_group") or "")].append(row)

        selected_bvids: set[str] = set()
        for topic_rows in rows_by_topic.values():
            # Stable multi-pass sorting mirrors relevance DESC, timestamp DESC,
            # view_count DESC, bvid ASC without letting candidate_tier leak in.
            topic_rows.sort(key=lambda row: str(row.get("bvid") or ""))
            topic_rows.sort(key=lambda row: int(row.get("view_count") or 0), reverse=True)
            topic_rows.sort(key=lambda row: str(row.get("last_scored_at") or ""), reverse=True)
            topic_rows.sort(
                key=lambda row: float(row.get("relevance_score") or 0.0),
                reverse=True,
            )
            selected_bvids.update(str(row.get("bvid") or "") for row in topic_rows[:topic_cap])
        return [row for row in materialized if str(row.get("bvid") or "") in selected_bvids]

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        """Return frontend-visible pool availability grouped by source family."""
        rows = self._load_available_pool_candidate_rows(
            max_per_topic_group=max_per_topic_group,
            xhs_self_nickname=xhs_self_nickname,
        )
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def _load_pool_raw_material_rows(self) -> list[dict[str, Any]]:
        """Load raw fresh material rows governed by the raw ceiling."""
        self._ensure_fresh_read()
        return self._load_pool_raw_material_rows_on(self.conn)

    def _load_pool_raw_material_rows_on(
        self,
        conn: sqlite3.Connection,
        *,
        _viewed_content_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Connection-aware raw content-cache rows governed by the ceiling."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = conn.execute(
            f"""
            SELECT
                bvid,
                source,
                source_platform,
                content_url,
                relevance_score,
                last_scored_at,
                pool_expression,
                pool_topic_label,
                style_key,
                topic_group,
                published_at,
                temporal_class,
                temporal_confidence,
                temporal_policy_version,
                temporal_validity_mode,
                temporal_valid_until,
                temporal_scope,
                temporal_state,
                temporal_evidence,
                temporal_next_review_at,
                temporal_evaluated_at,
                temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        viewed_content_keys = (
            self._recent_viewed_content_keys_on(conn)
            if _viewed_content_keys is None
            else _viewed_content_keys
        )
        rows: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if not self._temporal_eligibility_for_row(row_dict, now=now):
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            rows.append(row_dict)
        return rows

    def count_pool_raw_material_candidates(self) -> int:
        """Return raw fresh material count used for raw-ceiling headroom."""
        return (
            len(self._load_pool_raw_material_rows()) + self._count_pending_discovery_raw_material()
        )

    def _count_pool_raw_material_on(
        self,
        conn: sqlite3.Connection,
        *,
        _viewed_content_keys: set[str] | None = None,
    ) -> int:
        return len(
            self._load_pool_raw_material_rows_on(
                conn,
                _viewed_content_keys=_viewed_content_keys,
            )
        ) + len(self._load_temporally_eligible_admission_waiting_rows_on(conn))

    def count_pool_raw_material_by_source(self) -> dict[str, int]:
        """Return raw fresh material grouped by source family.

        Unlike ``count_pool_candidates_by_source()``, this intentionally counts
        pending/unopenable rows such as XHS notes waiting for ``xsec_token``.
        """
        counts: dict[str, int] = defaultdict(int)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        for row in self._load_temporally_eligible_admission_waiting_rows_on(self.conn):
            source_family = _pool_source_family(
                row.get("source_strategy"),
                row.get("source_platform"),
            )
            counts[source_family] += 1
        return dict(counts)

    def count_evaluated_discovery_candidates_by_source(self) -> dict[str, int]:
        """Return ``evaluated`` (admission-waiting) candidates grouped by family.

        Pool-share fairness (spec 2026-07-20, Phase 3): the rebalancer only
        evicts over-share rows when an under-share source actually has supply
        sitting in ``evaluated`` ready to take the freed slot.
        """
        self._ensure_fresh_read()
        counts: dict[str, int] = defaultdict(int)
        for row in self._load_temporally_eligible_admission_waiting_rows_on(self.conn):
            if str(row.get("status", "")) != "evaluated":
                continue
            source_family = _pool_source_family(
                row.get("source_strategy"),
                row.get("source_platform"),
            )
            counts[source_family] += 1
        return dict(counts)

    def count_admission_waiting_discovery_candidates_by_source(self) -> dict[str, int]:
        """Return admission-waiting candidates (any non-terminal stage) per family.

        Pool-share fairness (spec 2026-07-20, Phase 8 / D9): the rebalancer's
        "does an under-share source have supply waiting?" test must count
        ``pending_eval`` and ``evaluating`` too, not only ``evaluated`` —
        otherwise an orphan occupier pins the pool full, the under-share source
        never gets evaluated (coordinator idles at target), so its supply never
        reaches ``evaluated`` and eviction never fires (third chicken-and-egg).
        Counting the earlier stages breaks the loop; the second-round admission
        backfill keeps the global cap intact if a demoted row later fails eval.
        """
        self._ensure_fresh_read()
        counts: dict[str, int] = defaultdict(int)
        for row in self._load_temporally_eligible_admission_waiting_rows_on(self.conn):
            source_family = _pool_source_family(
                row.get("source_strategy"),
                row.get("source_platform"),
            )
            counts[source_family] += 1
        return dict(counts)

    def demote_lowest_ranked_pool_rows(self, *, source_family: str, limit: int) -> int:
        """Mark the *limit* lowest-ranked fresh visible rows of one family stale.

        Pool-share fairness (spec 2026-07-20, Phase 3): a gentle, per-tick
        rebalance frees a few slots held by an over-supplied source so an
        under-share source can be seated. Selection is the lowest
        ``relevance_score`` then oldest ``last_scored_at`` (quality never
        regresses — the best rows stay). Only ``fresh``, non-disliked,
        not-yet-recommended rows are touched, and they are set to the existing
        ``'stale'`` status (no new enum). Returns the number of rows demoted.
        """
        demote_limit = max(0, int(limit))
        family = str(source_family or "").strip()
        if demote_limit <= 0 or not family:
            return 0
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, relevance_score, last_scored_at,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """
        )
        candidates: list[dict[str, Any]] = []
        temporal_now = datetime.now(UTC)
        for row in cursor.fetchall():
            row_dict = dict(row)
            bvid = str(row_dict.get("bvid", "")).strip()
            if not bvid:
                continue
            if not self._temporal_eligibility_for_row(row_dict, now=temporal_now):
                continue
            if _pool_source_family(row_dict["source"], row_dict["source_platform"]) != family:
                continue
            candidates.append(row_dict)
        candidates.sort(
            key=lambda r: (
                float(r.get("relevance_score") or 0.0),
                str(r.get("last_scored_at") or ""),
            )
        )
        victims = [str(r["bvid"]) for r in candidates[:demote_limit]]
        if not victims:
            return 0
        placeholders = ", ".join("?" for _ in victims)
        result = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'stale'
            WHERE bvid IN ({placeholders})
              AND COALESCE(pool_status, 'fresh') = 'fresh'
            """,
            victims,
        )
        return result.rowcount

    def count_pool_readiness(
        self,
        *,
        xhs_self_nickname: str = "",
        _viewed_content_keys: set[str] | None = None,
    ) -> dict[str, int]:
        """Return pool inventory split by immediately servable and pending rows.

        ``available`` is the public "可换" count after the per-topic display
        window. ``copy_ready`` applies every serve gate but intentionally does
        not apply that ranking-diversity window: deeper ready rows become
        available as the current topic head is consumed, so treating them as
        missing copy would make a watermark drain same-topic backlog without
        increasing ``available``. ``raw`` is broad fresh material before
        readiness gates. ``pending`` is counted independently: recently viewed
        rows are unavailable, but they are not pending.
        """
        self._ensure_fresh_read()
        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        raw_cursor = self.conn.execute(
            f"""
            SELECT bvid, published_at, source, source_platform,
                   temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (*admission_params, *guard_params),
        )
        temporal_now = datetime.now(UTC)
        raw_count = sum(
            1
            for row in raw_cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
            and self._publication_date_row_is_eligible(dict(row))
        )
        pending_cursor = self.conn.execute(
            f"""
            SELECT
                bvid,
                content_id,
                source,
                source_platform,
                content_url,
                pool_expression,
                pool_topic_label,
                style_key,
                topic_group,
                published_at,
                temporal_class,
                temporal_confidence,
                temporal_policy_version,
                temporal_validity_mode,
                temporal_valid_until,
                temporal_scope,
                temporal_state,
                temporal_evidence,
                temporal_next_review_at,
                temporal_evaluated_at,
                temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (*admission_params, *guard_params),
        )
        viewed_content_keys = (
            self.get_recent_viewed_content_keys()
            if _viewed_content_keys is None
            else _viewed_content_keys
        )
        pending_count = 0
        for row in pending_cursor.fetchall():
            item = dict(row)
            if not self._temporal_eligibility_for_row(item, now=temporal_now):
                continue
            if not self._publication_date_row_is_eligible(item):
                continue
            if self._is_viewed_row(item, viewed_content_keys):
                continue
            if (
                not str(item.get("pool_expression") or "").strip()
                or not str(item.get("pool_topic_label") or "").strip()
                or not str(item.get("style_key") or "").strip()
                or not str(item.get("topic_group") or "").strip()
                or not _is_linkable_pool_source(
                    item.get("source"),
                    item.get("source_platform"),
                    item.get("content_url"),
                )
            ):
                pending_count += 1

        waiting_rows = self._load_temporally_eligible_admission_waiting_rows_on(self.conn)
        pending_eval_count = sum(
            str(row.get("status", "")) in {"pending_eval", "evaluating"} for row in waiting_rows
        )
        evaluated_pending_count = sum(
            str(row.get("status", "")) == "evaluated" for row in waiting_rows
        )
        discovery_pending_count = pending_eval_count + evaluated_pending_count

        available = self.count_pool_candidates(
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=viewed_content_keys,
        )
        copy_ready_rows = self._load_available_pool_candidate_rows_on(
            self.conn,
            max_per_topic_group=0,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=viewed_content_keys,
        )
        pending_copy_rows = self._load_admitted_pending_copy_rows_on(
            self.conn,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=viewed_content_keys,
        )
        pending_available_rows = self._select_admitted_pending_available_rows(
            pending_copy_rows,
            copy_ready_rows=copy_ready_rows,
        )
        return {
            "available": available,
            "copy_ready": len(copy_ready_rows),
            "raw": raw_count + discovery_pending_count,
            "pending": pending_count + discovery_pending_count,
            "admitted_pending_copy": len(pending_copy_rows),
            "admitted_pending_available": len(pending_available_rows),
            "pending_eval": pending_eval_count,
            "evaluated_pending": evaluated_pending_count,
        }

    def _load_admitted_pending_copy_rows_on(
        self,
        conn: sqlite3.Connection,
        *,
        xhs_self_nickname: str = "",
        limit: int | None = None,
        _viewed_content_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Load admitted, classified rows whose recommendation copy is incomplete."""

        admission_sql, admission_params = self._pool_admission_sql()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_threshold = self._dynamic_delight_threshold_on(
            conn, default_threshold=_DELIGHT_CLAIM_MIN_SCORE
        )
        delight_guard_sql = _delight_claim_guard_sql()
        max_rows = None if limit is None else max(0, int(limit))
        if max_rows == 0:
            return []
        cursor = conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(style_key, '') != ''
              AND COALESCE(topic_group, '') != ''
              AND (
                COALESCE(pool_expression, '') = ''
                OR COALESCE(pool_topic_label, '') = ''
              )
              AND (
                source_platform != 'xiaohongshu'
                OR content_url LIKE '%xsec_token=%'
              )
              {guard_sql}
              {delight_guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            """,
            (*admission_params, *guard_params, delight_threshold),
        )
        viewed_content_keys = (
            self._recent_viewed_content_keys_on(conn)
            if _viewed_content_keys is None
            else _viewed_content_keys
        )
        rows: list[dict[str, Any]] = []
        temporal_now = datetime.now(UTC)
        for row in cursor.fetchall():
            item = dict(row)
            if not str(item.get("bvid", "")).strip():
                continue
            if not self._temporal_eligibility_for_row(item, now=temporal_now):
                continue
            if self._is_viewed_row(item, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                item.get("source"),
                item.get("source_platform"),
                item.get("content_url"),
            ):
                continue
            rows.append(item)
            if max_rows is not None and len(rows) >= max_rows:
                break
        return rows

    @staticmethod
    def _select_admitted_pending_available_rows(
        pending_copy_rows: Sequence[Mapping[str, Any]],
        *,
        copy_ready_rows: Sequence[Mapping[str, Any]],
        max_per_topic_group: int = 3,
    ) -> list[dict[str, Any]]:
        """Return pending-copy rows that can increase public availability.

        Public availability exposes at most ``max_per_topic_group`` ready rows
        from each topic after canonical seen/linkability filtering. A pending
        row therefore adds one public slot only while its topic has remaining
        headroom. ``pending_copy_rows`` is already in canonical copy priority
        order, so this also produces a stable availability-first prefix.
        """

        topic_cap = max(0, int(max_per_topic_group))
        if topic_cap <= 0:
            return [dict(row) for row in pending_copy_rows]

        ready_by_topic: defaultdict[str, int] = defaultdict(int)
        for row in copy_ready_rows:
            ready_by_topic[str(row.get("topic_group") or "")] += 1

        selected_by_topic: defaultdict[str, int] = defaultdict(int)
        selected: list[dict[str, Any]] = []
        for row in pending_copy_rows:
            topic_group = str(row.get("topic_group") or "")
            remaining = topic_cap - ready_by_topic[topic_group]
            if selected_by_topic[topic_group] >= max(0, remaining):
                continue
            selected.append(dict(row))
            selected_by_topic[topic_group] += 1
        return selected

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        """Return fresh pool counts grouped by discovery source family."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, source, source_platform, content_url,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, int] = defaultdict(int)
        temporal_now = datetime.now(UTC)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if (
                not bvid
                or not self._temporal_eligibility_for_row(row_dict, now=temporal_now)
                or self._is_viewed_row(row_dict, viewed_content_keys)
            ):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]:
        """Return fresh pool counts grouped by topic, style, and franchise."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, topic_group, style_key, franchise_key,
                   source, source_platform, content_url,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, dict[str, int]] = {
            "topic_group": defaultdict(int),
            "style_key": defaultdict(int),
            "franchise_key": defaultdict(int),
        }
        temporal_now = datetime.now(UTC)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if (
                not bvid
                or not self._temporal_eligibility_for_row(row_dict, now=temporal_now)
                or self._is_viewed_row(row_dict, viewed_content_keys)
            ):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            for axis in ("topic_group", "style_key", "franchise_key"):
                value = str(row[axis] or "").strip()
                if value:
                    counts[axis][value] += 1
        return {axis: dict(axis_counts) for axis, axis_counts in counts.items()}

    def get_pool_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        """Per-platform ``topic_group`` counts of fresh servable pool rows (P3.1).

        Same servable filter as :meth:`get_pool_distribution_counts`, but keyed by
        ``source_platform`` → ``{platform: {topic_group: count}}`` so the keyword
        planner can avoid topics saturated *on that platform* instead of pool-wide
        (a topic piled up on B站 may be absent on 小红书). Returns ``{}`` on error.
        """
        try:
            admission_sql, admission_params = self._pool_admission_sql()
            cursor = self.conn.execute(
                f"""
                SELECT bvid, topic_group, style_key, franchise_key,
                       source, source_platform, content_url,
                       published_at, temporal_class, temporal_confidence,
                       temporal_policy_version, temporal_validity_mode,
                       temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                       temporal_next_review_at, temporal_evaluated_at,
                       temporal_evidence_complete
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND NOT EXISTS (
                    SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
                  )
                """,
                admission_params,
            )
            viewed_content_keys = self.get_recent_viewed_content_keys()
        except Exception:
            logger.debug("get_pool_topic_counts_by_platform query failed", exc_info=True)
            return {}
        counts: dict[str, dict[str, int]] = {}
        temporal_now = datetime.now(UTC)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if (
                not bvid
                or not self._temporal_eligibility_for_row(row_dict, now=temporal_now)
                or self._is_viewed_row(row_dict, viewed_content_keys)
            ):
                continue
            if not _is_linkable_pool_source(
                row["source"], row["source_platform"], row["content_url"]
            ):
                continue
            platform = str(row["source_platform"] or "").strip()
            topic = str(row["topic_group"] or "").strip()
            if not platform or not topic:
                continue
            counts.setdefault(platform, defaultdict(int))[topic] += 1
        return {platform: dict(topics) for platform, topics in counts.items()}

    def get_admitted_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        """Per-platform ``topic_group`` counts of ALL admitted content (P3.3).

        Where :meth:`get_pool_topic_counts_by_platform` counts the *current
        servable pool* (a saturation signal — too much right now), this counts
        every non-disliked, linkable row that ever made it into the cache from
        each platform, served or not — a *supply-advantage* signal: which topics
        each platform has actually delivered for this user. The keyword planner
        feeds the top topics back as a data-driven complement to the static
        ``<supply_advantage>`` table (after subtracting the platform's current
        avoid set). Returns ``{}`` on error.
        """
        try:
            admission_sql, admission_params = self._pool_admission_sql()
            cursor = self.conn.execute(
                f"""
                SELECT topic_group, source, source_platform, content_url
                FROM content_cache
                WHERE COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(topic_group, '') != ''
                """,
                admission_params,
            )
        except Exception:
            logger.debug("get_admitted_topic_counts_by_platform query failed", exc_info=True)
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            if not _is_linkable_pool_source(
                row["source"], row["source_platform"], row["content_url"]
            ):
                continue
            platform = str(row["source_platform"] or "").strip()
            topic = str(row["topic_group"] or "").strip()
            if not platform or not topic:
                continue
            counts.setdefault(platform, defaultdict(int))[topic] += 1
        return {platform: dict(topics) for platform, topics in counts.items()}

    def canonicalize_topic_groups(self, canonical_map: dict[str, str]) -> int:
        """Rewrite ``content_cache.topic_group`` to canonical form per map.

        v0.3.56+: ``canonical_map`` is built by
        ``RecommendationEngine.prewarm_supergroup_embeddings`` and maps
        normalized (lowered + stripped) topic_group → canonical form.
        Without applying it to the database rows, the merge only fires
        at serve time and downstream analytics (``get_topic_group_samples``,
        per-topic counts in popup status) see the un-merged labels.

        Returns the number of rows actually updated. Empty input or all-
        identity mappings short-circuit to 0.
        """
        if not canonical_map:
            return 0
        # Bulk update: one statement per (src → dst) pair. Pure SQL,
        # no row-level fetch. WAL-friendly because we batch in a single
        # transaction. Only rewrites rows whose lowercased+trimmed
        # topic_group exactly matches the source key — case-preserving
        # storage stays intact for non-matching rows.
        total = 0
        for src, dst in canonical_map.items():
            if src == dst or not src or not dst:
                continue
            cursor = self._execute_write(
                """
                UPDATE content_cache
                SET topic_group = ?
                WHERE LOWER(TRIM(COALESCE(topic_group, ''))) = ?
                  AND COALESCE(topic_group, '') != ?
                """,
                (dst, src, dst),
            )
            total += cursor.rowcount or 0
        return total

    def count_pool_by_franchise(self) -> dict[str, int]:
        """Return ``{franchise_key_lower: count}`` for fresh pool items.

        Used by discovery's pool-wide franchise quota check (v0.3.50+)
        so a franchise that already has many items in the pool can't
        keep accumulating across discovery rounds. Empty franchise_key
        is excluded — most generic content has no IP signal and the
        quota is only meaningful for series / IP / UP-driven groups.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT LOWER(TRIM(franchise_key)) AS fk,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND franchise_key IS NOT NULL
              AND TRIM(franchise_key) != ''
            """,
            admission_params,
        )
        temporal_now = datetime.now(UTC)
        counts: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            if not self._temporal_eligibility_for_row(dict(row), now=temporal_now):
                continue
            franchise = str(row["fk"] or "")
            if franchise:
                counts[franchise] += 1
        return dict(counts)

    def get_distinct_topic_groups(self) -> list[str]:
        """Return distinct non-empty ``topic_group`` values in the fresh pool.

        Used by recommendation pre-warming so the embedding cache is hot
        before the popup hits ``serve()``. Cheap GROUP BY on a small
        column with no JOIN.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT topic_group, published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
            """,
            admission_params,
        )
        temporal_now = datetime.now(UTC)
        return list(
            dict.fromkeys(
                str(row["topic_group"])
                for row in cursor.fetchall()
                if row["topic_group"]
                and self._temporal_eligibility_for_row(dict(row), now=temporal_now)
            )
        )

    def get_active_pool_topic_groups(
        self,
        *,
        limit: int = 30,
        min_count: int = 2,
    ) -> list[str]:
        """Return the top ``limit`` topic_group names currently in active pool.

        Used by ExploreStrategy to know which topics the pool already
        covers, so the LLM that generates explore domains can avoid
        re-proposing those (the v0.3.31 explore-blind-spot pattern).
        Filters to groups with at least ``min_count`` members so a
        single one-off item doesn't block exploration of an actually-
        empty area. Result is sorted by group size DESC.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT topic_group, published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        temporal_now = datetime.now(UTC)
        counts: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            if not self._temporal_eligibility_for_row(dict(row), now=temporal_now):
                continue
            counts[str(row["topic_group"])] += 1
        minimum = max(1, int(min_count))
        ordered = sorted(
            (group for group, count in counts.items() if count >= minimum),
            key=lambda group: (-counts[group], group),
        )
        return ordered[: max(1, int(limit))]

    def get_topic_group_samples(
        self,
        *,
        samples_per_group: int = 5,
        top_n_groups: int = 60,
    ) -> list[tuple[str, list[str]]]:
        """For each fresh-pool ``topic_group``, return up to N sample titles.

        Returns the top ``top_n_groups`` groups by member count (tie-break
        on highest in-group ``relevance_score``). Long-tail micro-topics
        (1-2 items) almost never show up together in a single 40-candidate
        recommendation batch, so investing API budget to merge-map them
        adds latency without affecting visible diversity.

        Used by the recommendation prewarmer to build an accurate
        supergroup-merge map: short Chinese labels (``赛博朋克``,
        ``动漫`` …) are catastrophically ambiguous in embedding space
        when embedded standalone — they need title-context disambiguation.
        Sample titles are picked top-by-``relevance_score`` within each
        group, so the input is reasonably stable while the pool is steady.
        """
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT topic_group, title, relevance_score,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
              AND COALESCE(title, '') != ''
            ORDER BY topic_group, relevance_score DESC, bvid
            """,
            admission_params,
        )
        by_group: dict[str, list[str]] = defaultdict(list)
        group_max_score: dict[str, float] = {}
        group_count: dict[str, int] = defaultdict(int)
        temporal_now = datetime.now(UTC)
        for row in cursor.fetchall():
            if not self._temporal_eligibility_for_row(dict(row), now=temporal_now):
                continue
            group = str(row["topic_group"]).strip()
            title = str(row["title"]).strip()
            if not group or not title:
                continue
            group_count[group] += 1
            score = float(row["relevance_score"] or 0.0)
            if score > group_max_score.get(group, -1.0):
                group_max_score[group] = score
            if len(by_group[group]) < samples_per_group:
                by_group[group].append(title)

        # Rank groups by member count desc, score desc, label asc (stable).
        ranked = sorted(
            by_group.keys(),
            key=lambda g: (-group_count[g], -group_max_score.get(g, 0.0), g),
        )
        return [(group, by_group[group]) for group in ranked[:top_n_groups]]

    @staticmethod
    def _clean_pool_quotas(quotas: Mapping[str, int]) -> dict[str, int]:
        clean: dict[str, int] = {}
        for source, quota in quotas.items():
            try:
                clean[_pool_source_family("", source)] = max(0, int(quota))
            except (TypeError, ValueError):
                continue
        return clean

    def _plan_stale_trim_on(
        self,
        conn: sqlite3.Connection,
        *,
        protected_ids: set[str],
        max_age_days: int,
    ) -> _ContentTrimPlan:
        rows = conn.execute(
            """
            SELECT bvid
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND discovered_at < datetime('now', '-' || ? || ' days')
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY discovered_at ASC, bvid ASC
            """,
            (max_age_days,),
        ).fetchall()
        stale_ids = [str(row["bvid"]) for row in rows]
        return _ContentTrimPlan(
            victim_bvids=tuple(bvid for bvid in stale_ids if bvid not in protected_ids),
            deferred=sum(bvid in protected_ids for bvid in stale_ids),
        )

    def _plan_explore_cluster_trim_on(
        self,
        conn: sqlite3.Connection,
        *,
        protected_ids: set[str],
        max_per_cluster: int,
    ) -> _ContentTrimPlan:
        admission_sql, admission_params = self._pool_admission_sql()
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT bvid, title, topic_key, relevance_score, last_scored_at,
                       published_at, temporal_class, temporal_confidence,
                       temporal_policy_version, temporal_validity_mode,
                       temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                       temporal_next_review_at, temporal_evaluated_at,
                       temporal_evidence_complete
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(source, '') = 'explore'
                """,
                admission_params,
            ).fetchall()
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        temporal_now = datetime.now(UTC)
        for row in rows:
            if not self._temporal_eligibility_for_row(row, now=temporal_now):
                continue
            cluster = self._explore_risk_cluster(row)
            if cluster:
                grouped[cluster].append(row)
        victims: list[str] = []
        deferred = 0
        cap = max(0, max_per_cluster)
        for items in grouped.values():
            protected = [row for row in items if str(row["bvid"]) in protected_ids]
            deferred += max(0, len(protected) - cap)
            remaining = max(0, cap - len(protected))
            unprotected = sorted(
                (row for row in items if str(row["bvid"]) not in protected_ids),
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            victims.extend(str(row["bvid"]) for row in unprotected[remaining:])
        return _ContentTrimPlan(tuple(victims), deferred)

    def _plan_topic_trim_on(
        self,
        conn: sqlite3.Connection,
        *,
        protected_ids: set[str],
        max_per_topic_group: int,
    ) -> _ContentTrimPlan:
        if max_per_topic_group <= 0:
            return _ContentTrimPlan()
        admission_sql, admission_params = self._pool_admission_sql()
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT bvid, source, source_platform, content_url, topic_group,
                       relevance_score, last_scored_at, pool_expression,
                       pool_topic_label, style_key,
                       published_at, temporal_class, temporal_confidence,
                       temporal_policy_version, temporal_validity_mode,
                       temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                       temporal_next_review_at, temporal_evaluated_at,
                       temporal_evidence_complete
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND {admission_sql}
                  AND COALESCE(topic_group, '') != ''
                  AND NOT EXISTS (
                    SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
                  )
                """,
                admission_params,
            ).fetchall()
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        temporal_now = datetime.now(UTC)
        for row in rows:
            if not self._temporal_eligibility_for_row(row, now=temporal_now):
                continue
            if not self._content_is_ready_reserve(row):
                continue
            grouped[str(row["topic_group"]).strip().lower()].append(row)
        victims: list[str] = []
        deferred = 0
        for items in grouped.values():
            protected = [row for row in items if str(row["bvid"]) in protected_ids]
            deferred += max(0, len(protected) - max_per_topic_group)
            remaining = max(0, max_per_topic_group - len(protected))
            unprotected = sorted(
                (row for row in items if str(row["bvid"]) not in protected_ids),
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            victims.extend(str(row["bvid"]) for row in unprotected[remaining:])
        return _ContentTrimPlan(tuple(victims), deferred)

    def _plan_source_trim_on(
        self,
        conn: sqlite3.Connection,
        *,
        protected_ids: set[str],
        source_share_quotas: Mapping[str, int],
        _viewed_content_keys: set[str] | None = None,
    ) -> _ContentTrimPlan:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._load_pool_raw_material_rows_on(
            conn,
            _viewed_content_keys=_viewed_content_keys,
        ):
            if not self._content_is_ready_reserve(row):
                continue
            grouped[_pool_source_family(row["source"], row["source_platform"])].append(row)
        victims: list[str] = []
        deferred = 0
        for family, quota in source_share_quotas.items():
            items = grouped.get(family, [])
            protected = [row for row in items if str(row["bvid"]) in protected_ids]
            deferred += max(0, len(protected) - quota)
            remaining = max(0, quota - len(protected))
            unprotected = sorted(
                (row for row in items if str(row["bvid"]) not in protected_ids),
                key=self._pool_trim_keep_key,
            )
            victims.extend(str(row["bvid"]) for row in unprotected[remaining:])
        return _ContentTrimPlan(tuple(victims), deferred)

    @staticmethod
    def _content_is_ready_reserve(row: Mapping[str, Any]) -> bool:
        return all(
            str(row.get(field, "") or "").strip()
            for field in ("pool_expression", "pool_topic_label", "style_key", "topic_group")
        ) and _is_linkable_pool_source(
            row.get("source"),
            row.get("source_platform"),
            row.get("content_url"),
        )

    def _raw_victim_key(
        self,
        row: Mapping[str, Any],
        *,
        family_counts: Mapping[str, int],
        quotas: Mapping[str, int],
        candidate: bool = False,
    ) -> tuple[int, float, float, int, str]:
        source = row.get("source_strategy" if candidate else "source", "")
        family = _pool_source_family(source, row.get("source_platform", ""))
        quota = quotas.get(family)
        over_quota = quota is not None and family_counts.get(family, 0) > quota
        timestamp = row.get("last_seen_at" if candidate else "last_scored_at", "")
        stable_id = row.get("id" if candidate else "bvid", "")
        return (
            0 if over_quota else 1,
            float(row.get("relevance_score", 0.0) or 0.0),
            self._sort_timestamp_score(str(timestamp or "")),
            0 if str(source or "").strip().lower() == "explore" else 1,
            str(stable_id),
        )

    def _plan_raw_trim_on(
        self,
        conn: sqlite3.Connection,
        *,
        protected_ids: set[str],
        raw_ceiling: int,
        raw_source_share_quotas: Mapping[str, int],
        _viewed_content_keys: set[str] | None = None,
    ) -> _RawTrimPlan:
        content_rows = self._load_pool_raw_material_rows_on(
            conn,
            _viewed_content_keys=_viewed_content_keys,
        )
        candidate_rows = [
            row for row in self._load_temporally_eligible_admission_waiting_rows_on(conn)
        ]
        raw_count = len(content_rows) + len(candidate_rows)
        excess = max(0, raw_count - raw_ceiling)
        if excess <= 0:
            return _RawTrimPlan()

        family_counts: dict[str, int] = defaultdict(int)
        for row in content_rows:
            family_counts[_pool_source_family(row["source"], row["source_platform"])] += 1
        for row in candidate_rows:
            family_counts[_pool_source_family(row["source_strategy"], row["source_platform"])] += 1

        unprotected_content = [row for row in content_rows if str(row["bvid"]) not in protected_ids]
        unready = sorted(
            (row for row in unprotected_content if not self._content_is_ready_reserve(row)),
            key=lambda row: self._raw_victim_key(
                row,
                family_counts=family_counts,
                quotas=raw_source_share_quotas,
            ),
        )
        pending = sorted(
            (
                row
                for row in candidate_rows
                if row["status"] == "pending_eval" and row["claim_token"] is None
            ),
            key=lambda row: self._raw_victim_key(
                row,
                family_counts=family_counts,
                quotas=raw_source_share_quotas,
                candidate=True,
            ),
        )
        evaluated = sorted(
            (
                row
                for row in candidate_rows
                if row["status"] == "evaluated" and row["claim_token"] is None
            ),
            key=lambda row: self._raw_victim_key(
                row,
                family_counts=family_counts,
                quotas=raw_source_share_quotas,
                candidate=True,
            ),
        )
        ready_reserve = sorted(
            (row for row in unprotected_content if self._content_is_ready_reserve(row)),
            key=lambda row: self._raw_victim_key(
                row,
                family_counts=family_counts,
                quotas=raw_source_share_quotas,
            ),
        )
        ordered: list[tuple[str, dict[str, Any]]] = [
            *(("content", row) for row in unready),
            *(("candidate", row) for row in pending),
            *(("candidate", row) for row in evaluated),
            *(("content", row) for row in ready_reserve),
        ]
        selected = ordered[:excess]
        content_ids = tuple(str(row["bvid"]) for kind, row in selected if kind == "content")
        candidate_statuses = tuple(
            (int(row["id"]), str(row["status"])) for kind, row in selected if kind == "candidate"
        )
        return _RawTrimPlan(
            content_bvids=content_ids,
            candidate_ids=tuple(candidate_id for candidate_id, _ in candidate_statuses),
            candidate_statuses=candidate_statuses,
            untrimmed_excess=max(0, excess - len(selected)),
        )

    @staticmethod
    def _apply_content_status_on(
        conn: sqlite3.Connection,
        bvids: Sequence[str],
        *,
        status: str,
    ) -> int:
        if not bvids:
            return 0
        placeholders = ", ".join("?" for _ in bvids)
        cursor = conn.execute(
            f"""
            UPDATE content_cache
            SET pool_status = ?
            WHERE bvid IN ({placeholders})
              AND COALESCE(pool_status, 'fresh') = 'fresh'
            """,
            (status, *bvids),
        )
        return int(cursor.rowcount)

    def _apply_content_suppression_on(
        self,
        conn: sqlite3.Connection,
        bvids: Sequence[str],
    ) -> int:
        return self._apply_content_status_on(conn, bvids, status="suppressed")

    def _apply_raw_trim_on(self, conn: sqlite3.Connection, plan: _RawTrimPlan) -> None:
        self._apply_content_suppression_on(conn, plan.content_bvids)
        if not plan.candidate_ids:
            return
        placeholders = ", ".join("?" for _ in plan.candidate_ids)
        conn.execute(
            f"""
            UPDATE discovery_candidates
            SET status = 'trimmed_capacity',
                eval_error = ?,
                claimed_at = NULL,
                claim_token = NULL
            WHERE id IN ({placeholders})
              AND status IN ('pending_eval', 'evaluated')
              AND claim_token IS NULL
            """,
            ("pool_raw_ceiling", *plan.candidate_ids),
        )

    @staticmethod
    def _validate_pool_maintenance_invariant(
        *,
        available_before: int,
        available_after: int,
        target: int,
    ) -> None:
        minimum = min(available_before, target)
        if available_after < minimum:
            raise PoolMaintenanceInvariantError(
                f"available inventory fell below protected floor: "
                f"before={available_before} after={available_after} target={target}"
            )

    def _recover_suppressed_pool_inventory_on(
        self,
        conn: sqlite3.Connection,
        *,
        deficit: int,
        source_share_quotas: Mapping[str, int],
        xhs_self_nickname: str,
        max_restore: int = _POOL_MAINTENANCE_BATCH_SIZE,
        _viewed_content_keys: set[str] | None = None,
    ) -> list[str]:
        """Restore a bounded set of paid-for rows without rescanning per row.

        The former implementation re-ran the full servability window after
        every restored row. A 300-row recovery therefore performed hundreds
        of canonical availability scans while holding ``BEGIN IMMEDIATE``.
        We model
        the availability/source counters in memory; the caller then validates
        the batch against one canonical availability scan and reverts any
        speculative rows that did not produce net-new inventory.
        """
        clean_deficit = max(0, int(deficit))
        clean_limit = max(0, int(max_restore))
        if clean_deficit <= 0 or clean_limit <= 0:
            return []

        available_rows = self._load_available_pool_candidate_rows_on(
            conn,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=_viewed_content_keys,
        )
        desired_available = len(available_rows) + clean_deficit
        current_family_count: dict[str, int] = defaultdict(int)
        current_topic_count: dict[str, int] = defaultdict(int)
        for row in available_rows:
            current_family_count[
                _pool_source_family(row.get("source"), row.get("source_platform"))
            ] += 1
            topic = str(row.get("topic_group", "") or "").strip().lower()
            if topic:
                current_topic_count[topic] += 1

        where_clause, where_params = self._pool_servable_where_clause_on(
            conn,
            xhs_self_nickname,
            pool_status="suppressed",
        )
        candidate_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE {where_clause}
                  AND recommended_at IS NULL
                """,
                where_params,
            ).fetchall()
        ]
        viewed_content_keys = (
            self._recent_viewed_content_keys_on(conn)
            if _viewed_content_keys is None
            else _viewed_content_keys
        )
        temporal_now = datetime.now(UTC)
        eligible_rows = [
            row
            for row in candidate_rows
            if str(row.get("bvid", "")).strip()
            and self._temporal_eligibility_for_row(row, now=temporal_now)
            and not self._is_viewed_row(row, viewed_content_keys)
            and _is_linkable_pool_source(
                row.get("source"),
                row.get("source_platform"),
                row.get("content_url"),
            )
        ]
        restored_ids: list[str] = []
        remaining_rows = list(eligible_rows)
        simulated_available = len(available_rows)
        # Public pool availability uses the default per-topic window of three.
        availability_topic_cap = 3
        while (
            remaining_rows
            and len(restored_ids) < clean_limit
            and simulated_available < desired_available
        ):
            fillable_rows = [
                row
                for row in remaining_rows
                if current_topic_count[str(row.get("topic_group", "") or "").strip().lower()]
                < availability_topic_cap
            ]
            if not fillable_rows:
                break
            row = min(
                fillable_rows,
                key=lambda candidate: (
                    0
                    if current_family_count[
                        _pool_source_family(
                            candidate.get("source"), candidate.get("source_platform")
                        )
                    ]
                    < source_share_quotas.get(
                        _pool_source_family(
                            candidate.get("source"), candidate.get("source_platform")
                        ),
                        0,
                    )
                    else 1,
                    -float(candidate.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(candidate.get("last_scored_at", "") or "")),
                    str(candidate.get("bvid", "")),
                ),
            )
            remaining_rows.remove(row)
            bvid = str(row["bvid"])
            cursor = conn.execute(
                """
                UPDATE content_cache
                SET pool_status = 'fresh'
                WHERE bvid = ? AND pool_status = 'suppressed'
                """,
                (bvid,),
            )
            if cursor.rowcount:
                restored_ids.append(bvid)
                topic = str(row.get("topic_group", "") or "").strip().lower()
                simulated_available += 1
                current_topic_count[topic] += 1
                current_family_count[
                    _pool_source_family(row.get("source"), row.get("source_platform"))
                ] += 1
        return restored_ids

    def maintain_pool_inventory(
        self,
        *,
        target: int,
        raw_ceiling: int,
        source_share_quotas: Mapping[str, int],
        raw_source_share_quotas: Mapping[str, int] | None = None,
        max_per_topic_group: int = 3,
        max_per_explore_cluster: int = 3,
        stale_max_age_days: int = 14,
        xhs_self_nickname: str = "",
        recover_suppressed: bool = True,
        max_mutations: int = _POOL_MAINTENANCE_BATCH_SIZE,
    ) -> PoolMaintenanceResult:
        """Maintain pool diversity and raw capacity in one bounded transaction.

        At most ``max_mutations`` rows are changed per call. The async runtime
        commits each batch, yields the event loop, then schedules another batch
        when ``PoolMaintenanceResult.has_more`` is true.
        """
        total_started = time.perf_counter()
        clean_target = max(0, int(target))
        clean_raw_ceiling = max(0, int(raw_ceiling))
        clean_topic_cap = max(0, int(max_per_topic_group))
        clean_explore_cap = max(0, int(max_per_explore_cluster))
        clean_stale_days = max(0, int(stale_max_age_days))
        clean_source_quotas = self._clean_pool_quotas(source_share_quotas)
        clean_raw_source_quotas = self._clean_pool_quotas(
            raw_source_share_quotas or source_share_quotas
        )
        mutation_budget = max(1, min(500, int(max_mutations)))
        conn: sqlite3.Connection | None = None
        snapshot_acquired = False
        available_before = 0
        raw_before = 0
        protected_ids: set[str] = set()
        recovery_budget = 0
        lock_wait_ms = 0.0
        recovery_ms = 0.0
        stale_trim_ms = 0.0
        explore_trim_ms = 0.0
        topic_trim_ms = 0.0
        source_trim_ms = 0.0
        raw_trim_ms = 0.0
        write_ms = 0.0
        temporal_due_count = 0
        temporal_due_has_more = False
        try:
            conn = self.open_connection()
            if isinstance(conn, sqlite3.Connection):
                conn.execute(f"PRAGMA busy_timeout = {_MAINTENANCE_DB_BUSY_TIMEOUT_MS}")
            lock_started = time.perf_counter()
            conn.execute("BEGIN IMMEDIATE")
            lock_wait_ms = (time.perf_counter() - lock_started) * 1000.0
            temporal_due_count, temporal_due_has_more = (
                self._transition_temporally_due_pool_items_on(
                    conn,
                    now=datetime.now(UTC),
                    limit=mutation_budget,
                )
            )
            maintenance_budget = max(0, mutation_budget - temporal_due_count)
            # All maintenance reads share one write transaction, so the view
            # event snapshot cannot change underneath us. Parse it once: the
            # source-aware extractor is Python-heavy and repeated scans used
            # to dominate otherwise bounded batches.
            viewed_content_keys = self._recent_viewed_content_keys_on(conn)
            before_rows = self._load_available_pool_candidate_rows_on(
                conn,
                xhs_self_nickname=xhs_self_nickname,
                _viewed_content_keys=viewed_content_keys,
            )
            available_before = len(before_rows)
            snapshot_acquired = True
            raw_before = self._count_pool_raw_material_on(
                conn,
                _viewed_content_keys=viewed_content_keys,
            )
            protected_ids = {
                str(row["bvid"]) for row in before_rows[: min(len(before_rows), clean_target)]
            }
            recovered_ids: list[str] = []
            # Suppressed rows do not count as raw material until restored. Never
            # restore through an already-exhausted raw ceiling: doing so used to
            # alternate forever with the next batch's capacity trim while the
            # frontend-visible availability remained unchanged.
            recovery_budget = min(
                maintenance_budget,
                max(0, clean_raw_ceiling - raw_before),
            )
            if recover_suppressed and recovery_budget > 0:
                phase_started = time.perf_counter()
                recovered_ids = self._recover_suppressed_pool_inventory_on(
                    conn,
                    deficit=max(0, clean_target - available_before),
                    source_share_quotas=clean_source_quotas,
                    xhs_self_nickname=xhs_self_nickname,
                    max_restore=recovery_budget,
                    _viewed_content_keys=viewed_content_keys,
                )
                recovery_ms = (time.perf_counter() - phase_started) * 1000.0
                recovered_available_rows = self._load_available_pool_candidate_rows_on(
                    conn,
                    xhs_self_nickname=xhs_self_nickname,
                    _viewed_content_keys=viewed_content_keys,
                )
                recovered_set = set(recovered_ids)
                visible_recovered = {
                    str(row["bvid"])
                    for row in recovered_available_rows
                    if str(row["bvid"]) in recovered_set
                }
                net_available_gain = max(0, len(recovered_available_rows) - available_before)
                # The in-memory recovery planner deliberately avoids a full
                # ranked-window scan per row. Its estimate can still be wrong
                # when a viewed/unlinkable row occupies a higher SQL topic
                # rank: restoring a lower-ranked suppressed row then changes
                # no canonical availability at all. Never let that speculative
                # write escape the transaction. A kept recovery must map
                # one-for-one to net-new canonical inventory; ambiguous
                # displacement is reverted as a batch and retried only after
                # the external pool fingerprint changes.
                if len(visible_recovered) != net_available_gain:
                    reverted_ids = recovered_set
                    visible_recovered = set()
                    recovered_available_rows = before_rows
                else:
                    reverted_ids = recovered_set - visible_recovered
                if reverted_ids:
                    self._apply_content_status_on(
                        conn,
                        sorted(reverted_ids),
                        status="suppressed",
                    )
                    logger.debug(
                        "pool maintenance reverted %s speculative recovery row(s) "
                        "that did not grow canonical availability",
                        len(reverted_ids),
                    )
                recovered_ids = [bvid for bvid in recovered_ids if bvid in visible_recovered]
                protected_ids.update(visible_recovered)
            initial_raw_rows = {
                str(row["bvid"]): row
                for row in self._load_pool_raw_material_rows_on(
                    conn,
                    _viewed_content_keys=viewed_content_keys,
                )
            }

            phase_started = time.perf_counter()
            stale_plan = self._plan_stale_trim_on(
                conn,
                protected_ids=protected_ids,
                max_age_days=clean_stale_days,
            )
            stale_trim_ms = (time.perf_counter() - phase_started) * 1000.0
            phase_started = time.perf_counter()
            explore_plan = self._plan_explore_cluster_trim_on(
                conn,
                protected_ids=protected_ids,
                max_per_cluster=clean_explore_cap,
            )
            explore_trim_ms = (time.perf_counter() - phase_started) * 1000.0
            phase_started = time.perf_counter()
            topic_plan = self._plan_topic_trim_on(
                conn,
                protected_ids=protected_ids,
                max_per_topic_group=clean_topic_cap,
            )
            topic_trim_ms = (time.perf_counter() - phase_started) * 1000.0
            phase_started = time.perf_counter()
            source_plan = self._plan_source_trim_on(
                conn,
                protected_ids=protected_ids,
                source_share_quotas=clean_source_quotas,
                _viewed_content_keys=viewed_content_keys,
            )
            source_trim_ms = (time.perf_counter() - phase_started) * 1000.0

            remaining_budget = max(0, maintenance_budget - len(recovered_ids))
            omitted_mutations = 0
            claimed_content_ids: set[str] = set()

            def _take_content(victim_bvids: Sequence[str]) -> set[str]:
                nonlocal omitted_mutations, remaining_budget
                eligible = [bvid for bvid in victim_bvids if bvid not in claimed_content_ids]
                claimed_content_ids.update(eligible)
                selected = eligible[:remaining_budget]
                omitted_mutations += len(eligible) - len(selected)
                remaining_budget -= len(selected)
                return set(selected)

            stale_ids = _take_content(stale_plan.victim_bvids)
            explore_ids = _take_content(explore_plan.victim_bvids)
            topic_ids = _take_content(topic_plan.victim_bvids)
            source_ids = _take_content(source_plan.victim_bvids)

            write_started = time.perf_counter()
            self._apply_content_status_on(conn, sorted(stale_ids), status="stale")
            self._apply_content_suppression_on(conn, sorted(explore_ids))
            self._apply_content_suppression_on(conn, sorted(topic_ids))
            self._apply_content_suppression_on(conn, sorted(source_ids))
            write_ms += (time.perf_counter() - write_started) * 1000.0

            phase_started = time.perf_counter()
            full_raw_plan = self._plan_raw_trim_on(
                conn,
                protected_ids=protected_ids,
                raw_ceiling=clean_raw_ceiling,
                raw_source_share_quotas=clean_raw_source_quotas,
                _viewed_content_keys=viewed_content_keys,
            )
            selected_raw_content = full_raw_plan.content_bvids[:remaining_budget]
            remaining_budget -= len(selected_raw_content)
            candidate_statuses_by_id = dict(full_raw_plan.candidate_statuses)
            selected_raw_candidate_ids = full_raw_plan.candidate_ids[:remaining_budget]
            selected_raw_candidate_statuses = tuple(
                (candidate_id, candidate_statuses_by_id[candidate_id])
                for candidate_id in selected_raw_candidate_ids
            )
            omitted_mutations += (
                len(full_raw_plan.content_bvids)
                - len(selected_raw_content)
                + len(full_raw_plan.candidate_ids)
                - len(selected_raw_candidate_ids)
            )
            raw_plan = _RawTrimPlan(
                content_bvids=selected_raw_content,
                candidate_ids=selected_raw_candidate_ids,
                candidate_statuses=selected_raw_candidate_statuses,
                untrimmed_excess=full_raw_plan.untrimmed_excess,
            )
            raw_trim_ms = (time.perf_counter() - phase_started) * 1000.0
            write_started = time.perf_counter()
            self._apply_raw_trim_on(conn, raw_plan)
            write_ms += (time.perf_counter() - write_started) * 1000.0
            after_rows = self._load_available_pool_candidate_rows_on(
                conn,
                xhs_self_nickname=xhs_self_nickname,
                _viewed_content_keys=viewed_content_keys,
            )
            raw_after = self._count_pool_raw_material_on(
                conn,
                _viewed_content_keys=viewed_content_keys,
            )
            self._validate_pool_maintenance_invariant(
                available_before=available_before,
                available_after=len(after_rows),
                target=clean_target,
            )

            all_content_victims = (
                stale_ids | explore_ids | topic_ids | source_ids | set(raw_plan.content_bvids)
            )
            candidate_statuses = dict(raw_plan.candidate_statuses)
            trimmed_ready_reserve = sum(
                self._content_is_ready_reserve(initial_raw_rows[bvid])
                for bvid in all_content_victims
                if bvid in initial_raw_rows
            )
            trimmed_raw = sum(
                not self._content_is_ready_reserve(initial_raw_rows[bvid])
                for bvid in all_content_victims
                if bvid in initial_raw_rows
            ) + sum(status == "pending_eval" for status in candidate_statuses.values())
            trimmed_evaluated = sum(status == "evaluated" for status in candidate_statuses.values())
            trimmed_by_source: dict[str, int] = defaultdict(int)
            for bvid in all_content_victims:
                row = initial_raw_rows.get(bvid)
                if row is not None:
                    trimmed_by_source[
                        _pool_source_family(row["source"], row["source_platform"])
                    ] += 1
            if raw_plan.candidate_ids:
                placeholders = ", ".join("?" for _ in raw_plan.candidate_ids)
                for row in conn.execute(
                    f"""
                    SELECT source_platform, source_strategy
                    FROM discovery_candidates
                    WHERE id IN ({placeholders})
                    """,
                    raw_plan.candidate_ids,
                ).fetchall():
                    trimmed_by_source[
                        _pool_source_family(row["source_strategy"], row["source_platform"])
                    ] += 1

            recovered_suppressed = 0
            if recovered_ids:
                placeholders = ", ".join("?" for _ in recovered_ids)
                recovered_suppressed = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM content_cache
                        WHERE bvid IN ({placeholders}) AND pool_status = 'fresh'
                        """,
                        recovered_ids,
                    ).fetchone()[0]
                )

            commit_started = time.perf_counter()
            conn.commit()
            write_ms += (time.perf_counter() - commit_started) * 1000.0
            recovery_can_continue = (
                recover_suppressed
                and len(after_rows) < clean_target
                and raw_after < clean_raw_ceiling
                and (
                    (recovery_budget == 0 and raw_after < raw_before)
                    or (recovery_budget > 0 and len(recovered_ids) >= recovery_budget)
                )
            )
            result = PoolMaintenanceResult(
                available_before=available_before,
                available_after=len(after_rows),
                target=clean_target,
                protected_available=len(protected_ids),
                recovered_suppressed=recovered_suppressed,
                trimmed_stale=len(stale_ids),
                trimmed_explore_cluster=len(explore_ids),
                trimmed_ready_reserve=trimmed_ready_reserve,
                trimmed_evaluated=trimmed_evaluated,
                trimmed_raw=trimmed_raw,
                trimmed_by_source=dict(trimmed_by_source),
                deferred_topic_trim=topic_plan.deferred,
                deferred_source_trim=source_plan.deferred,
                deferred_stale_trim=stale_plan.deferred,
                deferred_explore_cluster_trim=explore_plan.deferred,
                raw_before=raw_before,
                raw_after=raw_after,
                raw_ceiling=clean_raw_ceiling,
                untrimmed_raw_excess=max(0, raw_after - clean_raw_ceiling),
                rolled_back=False,
                mutation_count=(
                    temporal_due_count
                    + len(recovered_ids)
                    + len(all_content_victims)
                    + len(raw_plan.candidate_ids)
                ),
                has_more=(temporal_due_has_more or omitted_mutations > 0 or recovery_can_continue),
                lock_wait_ms=lock_wait_ms,
                recovery_ms=recovery_ms,
                stale_trim_ms=stale_trim_ms,
                explore_trim_ms=explore_trim_ms,
                topic_trim_ms=topic_trim_ms,
                source_trim_ms=source_trim_ms,
                raw_trim_ms=raw_trim_ms,
                write_ms=write_ms,
                total_ms=(time.perf_counter() - total_started) * 1000.0,
            )
            if full_raw_plan.untrimmed_excess > 0:
                logger.warning(
                    "pool maintenance cannot trim protected/token-owned raw excess: %s",
                    full_raw_plan.untrimmed_excess,
                )
            return result
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            if not snapshot_acquired:
                if "locked" in str(exc).lower():
                    logger.info("pool maintenance deferred by SQLite writer: %s", exc)
                    raise PoolMaintenanceDeferredError(
                        "pool maintenance snapshot unavailable: writer busy"
                    ) from exc
                logger.error("pool maintenance snapshot unavailable: %s", exc)
                raise PoolMaintenanceSnapshotUnavailableError(
                    "pool maintenance snapshot unavailable"
                ) from exc
            logger.error("pool maintenance rolled back: %s", exc)
            return PoolMaintenanceResult(
                available_before=available_before,
                available_after=available_before,
                target=clean_target,
                protected_available=len(protected_ids),
                recovered_suppressed=0,
                trimmed_stale=0,
                trimmed_explore_cluster=0,
                trimmed_ready_reserve=0,
                trimmed_evaluated=0,
                trimmed_raw=0,
                trimmed_by_source={},
                deferred_topic_trim=0,
                deferred_source_trim=0,
                deferred_stale_trim=0,
                deferred_explore_cluster_trim=0,
                raw_before=raw_before,
                raw_after=raw_before,
                raw_ceiling=clean_raw_ceiling,
                untrimmed_raw_excess=max(0, raw_before - clean_raw_ceiling),
                rolled_back=True,
                reason=str(exc),
                lock_wait_ms=lock_wait_ms,
                recovery_ms=recovery_ms,
                stale_trim_ms=stale_trim_ms,
                explore_trim_ms=explore_trim_ms,
                topic_trim_ms=topic_trim_ms,
                source_trim_ms=source_trim_ms,
                raw_trim_ms=raw_trim_ms,
                write_ms=write_ms,
                total_ms=(time.perf_counter() - total_started) * 1000.0,
            )
        finally:
            if conn is not None:
                conn.close()

    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int:
        """Suppress excess fresh explore items from high-risk topic clusters."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, title, topic_key, relevance_score, last_scored_at,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(source, '') = 'explore'
            """,
            admission_params,
        )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            cluster = self._explore_risk_cluster(row)
            if not cluster:
                continue
            grouped[cluster].append(row)

        overflow_bvids: list[str] = []
        for items in grouped.values():
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            overflow_bvids.extend(
                str(row.get("bvid", "")).strip() for row in ranked[max(0, max_per_cluster) :]
            )

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        return len(clean_bvids)

    def trim_topic_group_overflow(self, *, max_per_group: int) -> int:
        """Suppress fresh items where any single ``topic_group`` exceeds *max_per_group*.

        Generalises the source-and-keyword-specific
        :meth:`trim_explore_cluster_overflow` to a cross-source, dynamic cap on
        every populated ``topic_group`` value. Without this, a single topic
        (e.g. ``人工智能``) can accumulate hundreds of fresh candidates as
        related_chain/search/explore each keep returning the same coarse group
        across rounds — m118's per-call ``_compress_topic_repeats`` doesn't
        compose across rounds, and the explore-only cluster cap doesn't see
        related_chain or search.

        Items with empty ``topic_group`` are ignored. Within an over-cap
        group, the highest-scored / most-recently-scored items are kept;
        the rest get ``pool_status='suppressed'``.

        v0.3.31+: emits an INFO log when something gets dropped, naming
        the over-flowing groups + how many items each lost. Without this,
        the function ran silently — operators couldn't tell whether the
        diversity machinery was actually cutting anything or sleeping.
        """
        if max_per_group <= 0:
            return 0

        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, topic_group, relevance_score, last_scored_at,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            admission_params,
        )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        if not rows:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            group = str(row.get("topic_group", "") or "").strip().lower()
            if not group:
                continue
            grouped[group].append(row)

        overflow_bvids: list[str] = []
        # v0.3.31+: track per-group drop counts for the INFO log
        drops_per_group: dict[str, int] = {}
        for group_name, items in grouped.items():
            if len(items) <= max_per_group:
                continue
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            losers = ranked[max_per_group:]
            drops_per_group[group_name] = len(losers)
            overflow_bvids.extend(str(row.get("bvid", "")).strip() for row in losers)

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

        # Top 10 most-trimmed groups so the log line stays readable.
        # Demoted to DEBUG: this runs once per minute from the refresh
        # tick. When the pool is steady-state and a single group
        # consistently sits ~8 items over the cap, the same line gets
        # logged 1440x per day at INFO. Caller can lift to INFO when
        # the trim shape actually changes (see refresh.enforce_pool_cap).
        top = sorted(drops_per_group.items(), key=lambda kv: -kv[1])[:10]
        logger.debug(
            "[diversity] trim_topic_group_overflow: cap=%d, dropped=%d items "
            "across %d over-cap groups, top: %s",
            max_per_group,
            len(clean_bvids),
            len(drops_per_group),
            ", ".join(f"{g}:{c}" for g, c in top),
        )
        return len(clean_bvids)

    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Suppress overflow fresh items so the pool does not exceed *target*.

        Ranking (what we keep): higher ``relevance_score`` > newer
        ``last_scored_at`` > non-``explore`` source > stable ``bvid``. Items
        already surfaced as recommendations are excluded from the count — the
        recommendation side treats the pool as a queue, so consumed rows are
        never trimmed here.

        When ``source_share_quotas`` is provided, the trim respects per-source-family
        share targets: items from source families already at or above their quota
        get suppressed *before* lower-scored items from under-quota sources.
        Without this, score-only trim systematically axes low-relevance
        sources (trending, explore) when high-relevance sources (search,
        related_chain) overflow — defeating the per-source diversity goal.
        Xiaohongshu extension channels (task/search/explore/profile) are
        collapsed under the single ``xiaohongshu`` family.
        """
        if target <= 0:
            return 0

        rows = self._load_pool_raw_material_rows()
        if len(rows) <= target:
            return 0

        ranked = sorted(
            rows,
            key=self._pool_trim_keep_key,
        )

        if source_share_quotas:
            # Three-tier protection so under-quota sources stay fully intact:
            #   protected: items from sources whose total ≤ quota, OR top-N
            #              items from sources whose total > quota (where N=quota)
            #   negotiable_tracked: bottom (total-quota) items from over-quota
            #              tracked sources
            #   negotiable_untracked: items from sources without a declared
            #              share — eligible to be cut before touching protected.
            # Order for the final keep walk: protected → negotiable_untracked
            # → negotiable_tracked.  This ensures trending (under quota) stays
            # 100% protected even when sum of in_quota > target due to
            # untracked sources eating slots.
            counts_per_source: dict[str, int] = defaultdict(int)
            for row in rows:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                counts_per_source[source_family] += 1

            protected: list[dict[str, Any]] = []
            negotiable_tracked: list[dict[str, Any]] = []
            negotiable_untracked: list[dict[str, Any]] = []
            seen: dict[str, int] = defaultdict(int)
            for row in ranked:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                quota = source_share_quotas.get(source_family)
                if quota is None:
                    negotiable_untracked.append(row)
                    continue
                if counts_per_source[source_family] <= quota:
                    # entire source under quota — every item protected
                    protected.append(row)
                else:
                    # over quota: top `quota` items protected, rest negotiable
                    if seen[source_family] < quota:
                        protected.append(row)
                        seen[source_family] += 1
                    else:
                        negotiable_tracked.append(row)
            ranked = protected + negotiable_untracked + negotiable_tracked

        overflow_rows = ranked[target:]
        overflow_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        # v0.3.31+: log per-source breakdown so operators see whether the
        # quota guard is biting (e.g. "explore overflowing 80%" → fix the
        # discovery cycle, not the recommender).
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_to_target_count: target=%d, before=%d, "
            "suppressed=%d, by-source: %s",
            target,
            len(rows),
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int:
        """Suppress fresh rows that exceed platform-family pool quotas.

        ``trim_pool_to_target_count`` caps the total pool size. This pass caps
        each tracked platform family independently, so an over-filled family
        cannot occupy capacity reserved for another source while the total pool
        is still below target.
        """
        clean_quotas: dict[str, int] = {}
        for source_family, quota in source_share_quotas.items():
            try:
                clean_quotas[str(source_family)] = max(0, int(quota))
            except (TypeError, ValueError):
                continue
        if not clean_quotas:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            if source_family in clean_quotas:
                grouped[source_family].append(row)

        overflow_rows: list[dict[str, Any]] = []
        for source_family, rows in grouped.items():
            quota = clean_quotas[source_family]
            if len(rows) <= quota:
                continue
            ranked = sorted(
                rows,
                key=self._pool_trim_keep_key,
            )
            overflow_rows.extend(ranked[quota:])

        clean_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in clean_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_source_overflow: suppressed=%d, by-source: %s",
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Move suppressed candidates back to fresh for under-quota source families.

        This is a source-balance repair pass for pools that are already full but
        uneven. It only reactivates rows that are otherwise eligible for the
        recommendation pool. Reactivation is driven by frontend-available
        deficits, but bounded by raw-material headroom so pending rows already
        occupying a source's raw ceiling do not trigger more fresh inventory.
        """
        if target <= 0 or not source_share_quotas:
            return 0

        current_counts = self.count_pool_available_candidates_by_source()
        raw_counts = self.count_pool_raw_material_by_source()
        raw_quotas = raw_source_share_quotas or source_share_quotas
        deficits = {
            source_family: min(
                min(target, max(0, int(quota))) - int(current_counts.get(source_family, 0)),
                max(
                    0,
                    int(raw_quotas.get(source_family, quota))
                    - int(raw_counts.get(source_family, 0)),
                ),
            )
            for source_family, quota in source_share_quotas.items()
            if int(quota) > 0
        }
        deficits = {source: deficit for source, deficit in deficits.items() if deficit > 0}
        if not deficits:
            return 0

        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT bvid, source, source_platform, content_url,
                   relevance_score, last_scored_at,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'suppressed'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND {admission_sql}
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                bvid ASC
            """,
            admission_params,
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        selected_bvids: list[str] = []
        selected_counts: dict[str, int] = defaultdict(int)
        target_selection_count = sum(deficits.values())
        temporal_now = datetime.now(UTC)

        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if (
                not bvid
                or not self._temporal_eligibility_for_row(row_dict, now=temporal_now)
                or self._is_viewed_row(row_dict, viewed_content_keys)
            ):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            deficit = deficits.get(source_family, 0)
            if deficit <= 0 or selected_counts[source_family] >= deficit:
                continue
            selected_bvids.append(bvid)
            selected_counts[source_family] += 1
            if len(selected_bvids) >= target_selection_count:
                break

        if not selected_bvids:
            return 0

        placeholders = ", ".join("?" for _ in selected_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'fresh'
            WHERE bvid IN ({placeholders})
            """,
            selected_bvids,
        )
        return len(selected_bvids)

    @staticmethod
    def _balance_pool_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        """Round-robin sample from a relevance-ordered pool, balanced by content topic.

        Buckets by ``topic_group`` (with fallback to ``topic_key`` then a
        sentinel) so that one dominant topic in the relevance head can't
        crowd out the candidate window. Source/platform are intentionally
        ignored — content-side features drive richness, not provenance.

        The round-robin always runs (even when ``len(rows) <= limit``) so
        that the returned ordering is balanced for downstream callers
        that may sub-select; otherwise the SQL ordering can place several
        items of the same topic back-to-back at the top.
        """
        if limit <= 0 or len(rows) <= 1:
            return rows[:limit]

        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        topic_order: list[str] = []
        for row in rows:
            key = str(row.get("topic_group", "") or "").strip().lower()
            if not key:
                key = str(row.get("topic_key", "") or "").strip().lower()
            if not key:
                key = "unknown"
            if key not in buckets:
                topic_order.append(key)
            buckets[key].append(row)

        balanced: list[dict[str, Any]] = []
        while len(balanced) < limit:
            progressed = False
            for key in topic_order:
                bucket = buckets[key]
                if not bucket:
                    continue
                balanced.append(bucket.pop(0))
                progressed = True
                if len(balanced) >= limit:
                    break
            if not progressed:
                break
        return balanced[:limit]

    @staticmethod
    def _advance_seen_items_cursor_on(conn: sqlite3.Connection, event_id: int) -> None:
        if event_id <= 0:
            return
        conn.execute(
            """
            INSERT INTO seen_items_backfill_state (singleton, last_scanned_event_id)
            VALUES (1, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                last_scanned_event_id = MAX(
                    seen_items_backfill_state.last_scanned_event_id,
                    excluded.last_scanned_event_id
                )
            """,
            (event_id,),
        )

    def _invalidate_seen_state_cache(self) -> None:
        with self._seen_state_lock:
            self._seen_state_cache.clear()

    @classmethod
    def _upsert_seen_items_from_view_event_on(
        cls,
        conn: sqlite3.Connection,
        *,
        event_id: int,
        row: dict[str, Any],
        seen_at: str = "",
    ) -> bool:
        """Upsert every canonical identity carried by one view event."""
        if event_id <= 0:
            return False
        keys, _ = cls._extract_view_event_identities(row)
        canonical_rows: set[tuple[str, str, str]] = set()
        for key in keys:
            if ":" not in key:
                continue
            source_platform, content_id = key.split(":", 1)
            platform = canonical_source_platform(source_platform)
            content_id = content_id.strip()
            if not platform or not content_id:
                continue
            try:
                item_key = make_item_key(platform, content_id)
            except ValueError:
                continue
            canonical_rows.add((item_key, platform, content_id))
        for item_key, platform, content_id in canonical_rows:
            conn.execute(
                """
                INSERT INTO seen_items (
                    item_key,
                    source_platform,
                    content_id,
                    first_event_id,
                    last_event_id,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (
                    ?, ?, ?, ?, ?,
                    COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP),
                    COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP)
                )
                ON CONFLICT(item_key) DO UPDATE SET
                    first_event_id = MIN(
                        seen_items.first_event_id,
                        excluded.first_event_id
                    ),
                    last_event_id = MAX(
                        seen_items.last_event_id,
                        excluded.last_event_id
                    ),
                    first_seen_at = CASE
                        WHEN excluded.first_event_id < seen_items.first_event_id
                            THEN excluded.first_seen_at
                        ELSE seen_items.first_seen_at
                    END,
                    last_seen_at = CASE
                        WHEN excluded.last_event_id >= seen_items.last_event_id
                            THEN excluded.last_seen_at
                        ELSE seen_items.last_seen_at
                    END
                """,
                (
                    item_key,
                    platform,
                    content_id,
                    event_id,
                    event_id,
                    seen_at,
                    seen_at,
                ),
            )
        return bool(canonical_rows)

    def mark_items_seen(self, source_platform: str, content_ids: Iterable[str]) -> int:
        """Record an account-snapshot's contents as already consumed.

        Some evidence that the user has seen a piece of content arrives as a
        *list*, not an event: the favourites folder account sync fetches every
        6 hours is the whole set, and only newly-added entries ever become
        events. Everything the user favourited before OpenBiliClaw existed
        therefore had no event to derive from, and stayed recommendable forever.

        Snapshot rows carry ``first_event_id = 0`` because no single event
        produced them, and existing rows are left untouched — a real event's
        provenance and timestamps outrank a snapshot's. Returns the number of
        identities newly added.
        """
        platform = canonical_source_platform(source_platform)
        if not platform:
            return 0
        rows: set[tuple[str, str]] = set()
        for raw in content_ids:
            content_id = str(raw or "").strip()
            if not content_id:
                continue
            try:
                rows.add((make_item_key(platform, content_id), content_id))
            except ValueError:
                continue
        if not rows:
            return 0
        added = 0
        for item_key, content_id in sorted(rows):
            cursor = self.conn.execute(
                """
                INSERT INTO seen_items (
                    item_key,
                    source_platform,
                    content_id,
                    first_event_id,
                    last_event_id,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(item_key) DO NOTHING
                """,
                (item_key, platform, content_id),
            )
            added += int(cursor.rowcount or 0)
        self.conn.commit()
        if added:
            # The seen-state cache is keyed on MAX(last_event_id), which
            # snapshot rows deliberately leave at 0 — without this the new
            # identities would stay invisible until the next real event.
            self._invalidate_seen_state_cache()
        return added

    def get_seen_bvids(self) -> set[str]:
        """Return every Bilibili identity recorded in the durable seen ledger."""
        _, seen_bvids = self._seen_state_on(self.conn)
        return seen_bvids

    def get_seen_content_keys(self) -> set[str]:
        """Return all source-aware content identities ever recorded as viewed."""
        return self._seen_content_keys_on(self.conn)

    def _seen_content_keys_on(self, conn: sqlite3.Connection) -> set[str]:
        seen_keys, _ = self._seen_state_on(conn)
        return seen_keys

    def _seen_state_on(
        self,
        conn: sqlite3.Connection,
    ) -> tuple[set[str], set[str]]:
        """Materialize canonical seen keys and BVIDs from the durable ledger."""
        latest_row = conn.execute(
            "SELECT COALESCE(MAX(last_event_id), 0) AS latest_id FROM seen_items"
        ).fetchone()
        latest_id = int(latest_row["latest_id"] if latest_row else 0)
        with self._seen_state_lock:
            cached = self._seen_state_cache.get(latest_id)
        if cached is not None:
            cached_keys, cached_bvids = cached
            return set(cached_keys), set(cached_bvids)

        rows = conn.execute(
            """
            SELECT item_key, source_platform, content_id
            FROM seen_items
            ORDER BY item_key
            """
        ).fetchall()
        seen_keys = {str(row["item_key"]) for row in rows if str(row["item_key"] or "")}
        seen_bvids = {
            str(row["content_id"])
            for row in rows
            if canonical_source_platform(str(row["source_platform"] or ""))
            == _BILIBILI_SOURCE_FAMILY
            and str(row["content_id"] or "").startswith("BV")
        }
        seen_keys.update(seen_bvids)
        with self._seen_state_lock:
            self._seen_state_cache.clear()
            self._seen_state_cache[latest_id] = (
                frozenset(seen_keys),
                frozenset(seen_bvids),
            )
        return seen_keys, seen_bvids

    def get_recent_viewed_bvids(self, limit: int = 2000) -> set[str]:
        """Compatibility alias for the unbounded durable Bilibili seen set."""
        del limit
        return self.get_seen_bvids()

    def get_recent_viewed_content_keys(self, limit: int = 2000) -> set[str]:
        """Compatibility alias for the unbounded durable canonical seen set."""
        del limit
        return self.get_seen_content_keys()

    def _recent_viewed_content_keys_on(
        self,
        conn: sqlite3.Connection,
        *,
        limit: int = 2000,
    ) -> set[str]:
        """Compatibility alias for connection-aware durable seen identities."""
        del limit
        return self._seen_content_keys_on(conn)

    def _recent_viewed_state_on(
        self,
        conn: sqlite3.Connection,
        *,
        limit: int = 2000,
    ) -> tuple[set[str], set[str]]:
        """Compatibility alias for the connection-aware durable seen state."""
        del limit
        return self._seen_state_on(conn)

    @staticmethod
    def _explore_risk_cluster(row: dict[str, Any]) -> str:
        haystack = " ".join(
            [
                str(row.get("topic_key", "") or ""),
                str(row.get("title", "") or ""),
            ]
        ).lower()
        if not haystack.strip():
            return ""
        compact = re.sub(r"\s+", "", haystack)
        for cluster, keywords in _EXPLORE_HIGH_RISK_CLUSTERS:
            if any(keyword in compact for keyword in keywords):
                return cluster
        return ""

    @staticmethod
    def _sort_timestamp_score(value: str) -> float:
        if not value:
            return 0.0
        normalized = value.replace(" ", "T")
        try:
            from datetime import datetime

            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return 0.0

    def _pool_trim_keep_key(self, row: dict[str, Any]) -> tuple[int, int, float, float, int, str]:
        """Sort fresh raw material from most worth keeping to least.

        Raw-ceiling trims include pending rows, so servability has to outrank
        relevance: never keep an unopenable row over an openable one from the
        same trim candidate set just because the pending row has a higher score.
        """
        linkable = _is_linkable_pool_source(
            row.get("source"),
            row.get("source_platform"),
            row.get("content_url"),
        )
        ready = all(
            str(row.get(field, "") or "").strip()
            for field in ("pool_expression", "pool_topic_label", "style_key", "topic_group")
        )
        return (
            0 if linkable else 1,
            0 if ready else 1,
            -float(row.get("relevance_score", 0.0) or 0.0),
            -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
            1 if str(row.get("source", "") or "") == "explore" else 0,
            str(row.get("bvid", "")),
        )

    def mark_pool_items_shown(self, bvids: list[str]) -> None:
        """Mark discovery-pool items as already shown in recommendations."""
        clean_bvids = [item for item in bvids if item]
        if not clean_bvids:
            return
        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'shown',
                recommended_at = CURRENT_TIMESTAMP
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int:
        """Mark pool items older than *max_age_days* as stale."""
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'stale'
            WHERE pool_status = 'fresh'
              AND discovered_at < datetime('now', '-' || ? || ' days')
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            (max_age_days,),
        )
        return cursor.rowcount

    def purge_pool_by_disliked_topics(self, topics: list[str]) -> int:
        """Mark fresh pool candidates matching new dislikes as purged.

        Matching strategy (all case-sensitive at the SQLite layer — Chinese
        text makes case folding moot and ASCII matching still works):
          1. Exact match on ``topic_key``, ``topic_group``, or ``pool_topic_label``
          2. Substring match on ``title`` or ``pool_topic_label``
             (catches "鬼畜合集" when the dislike is "鬼畜")

        Only candidates in ``pool_status = 'fresh'`` are affected — historical
        rows (``shown``, ``feedbacked``, ``stale``) are preserved for audit.
        Already-recommended items are skipped so the recommendation history
        remains intact.

        Args:
            topics: Newly added disliked topics (stripped, non-empty strings).

        Returns:
            Number of rows transitioned to ``pool_status = 'purged_by_dislike'``.
        """
        clean = [t.strip() for t in topics if t and t.strip()]
        if not clean:
            return 0

        # Build the match clause dynamically. Use parameterized queries
        # throughout — topic values may contain SQL metacharacters that must
        # not be interpolated into the query string.
        exact_placeholders = ", ".join("?" for _ in clean)
        like_conditions = " OR ".join("title LIKE ? OR pool_topic_label LIKE ?" for _ in clean)

        params: list[Any] = []
        params.extend(clean)  # topic_key IN (...)
        params.extend(clean)  # topic_group IN (...)
        params.extend(clean)  # pool_topic_label IN (...)
        for topic in clean:
            like = f"%{topic}%"
            params.append(like)  # title LIKE ?
            params.append(like)  # pool_topic_label LIKE ?

        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
              AND (
                topic_key IN ({exact_placeholders})
                OR topic_group IN ({exact_placeholders})
                OR pool_topic_label IN ({exact_placeholders})
                OR {like_conditions}
              )
            """,
            params,
        )
        return cursor.rowcount

    def get_fresh_pool_candidates_for_purge_scan(
        self,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return fresh, not-yet-recommended pool candidates for a semantic scan.

        Returns only the fields needed for embedding-based matching:
        bvid, title, topic_key, topic_group, pool_topic_label.
        """
        max_rows = max(0, int(limit))
        if max_rows == 0:
            return []
        cursor = self.conn.execute(
            """
            SELECT bvid, title, topic_key, topic_group, pool_topic_label,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
            )
            ORDER BY discovered_at DESC
            """
        )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        return rows[:max_rows]

    def mark_pool_items_purged_by_dislike(self, bvids: list[str]) -> int:
        """Mark specified bvids as purged_by_dislike (only if currently fresh)."""
        clean = [b.strip() for b in bvids if b and b.strip()]
        if not clean:
            return 0
        placeholders = ", ".join("?" for _ in clean)
        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE bvid IN ({placeholders})
              AND COALESCE(pool_status, 'fresh') = 'fresh'
            """,
            clean,
        )
        return cursor.rowcount

    def mark_pool_purged_by_reinit(self) -> int:
        """Retire every active pool row after a forced re-init.

        A force re-init rebuilds the profile, so rows scored / copy-written
        under the previous profile must stop being served immediately —
        otherwise old recommendations linger and (because the backfill only
        tops up to a small target, ``_INIT_POOL_TARGET_COUNT``) crowd out
        fresh ones. Follows the ``purged_by_dislike`` precedent: rows stay in
        ``content_cache`` for audit / dedup but are excluded from the
        available-pool gate (which requires ``pool_status = 'fresh'``) and
        from serve / recall paths.

        Returns the number of rows retired.
        """
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'purged_by_reinit'
            WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """
        )
        return cursor.rowcount

    def get_pool_candidates_needing_evaluation(
        self,
        limit: int = 20,
        *,
        xhs_self_nickname: str = "",
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Claim unclassified fresh rows and due temporal-review holds.

        Targets items with empty ``style_key`` AND empty ``topic_group`` —
        typically content from non-bilibili sources (e.g. xiaohongshu) that
        was inserted directly into ``content_cache`` without passing through
        the discovery engine's ``evaluate_content`` pipeline.

        Selecting a hold atomically leases its next retry using bounded
        exponential backoff. A failed evaluator batch therefore cannot select
        the same recent row every refresh and starve later holds. A complete
        non-neutral review clears the lease in :meth:`cache_content`.
        """
        max_rows = max(0, int(limit))
        if max_rows == 0:
            return []
        explicit_now = now
        if explicit_now is not None and explicit_now.tzinfo is None:
            explicit_now = explicit_now.replace(tzinfo=UTC)
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        self._ensure_fresh_read()
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                connection = self.conn
                connection.execute("BEGIN IMMEDIATE")
                try:
                    effective_now = explicit_now or datetime.now(UTC)
                    now_text = effective_now.astimezone(UTC).isoformat().replace("+00:00", "Z")
                    rows = [
                        dict(row)
                        for row in connection.execute(
                            f"""
                            SELECT *
                            FROM content_cache
                            WHERE COALESCE(pool_status, 'fresh')
                                    IN ('fresh', 'temporal_review_hold')
                              AND COALESCE(feedback_type, '') != 'dislike'
                              AND (
                                    (
                                        pool_status = 'temporal_review_hold'
                                        AND (
                                            COALESCE(temporal_review_retry_at, '') = ''
                                            OR julianday(temporal_review_retry_at)
                                                <= julianday(?)
                                        )
                                    )
                                    OR (
                                        COALESCE(pool_status, 'fresh') = 'fresh'
                                        AND COALESCE(style_key, '') = ''
                                        AND COALESCE(topic_group, '') = ''
                                        AND COALESCE(relevance_score, 0) = 0
                                    )
                              )
                              {guard_sql}
                              AND NOT EXISTS (
                                SELECT 1
                                FROM recommendations AS r
                                WHERE r.bvid = content_cache.bvid
                              )
                            ORDER BY
                                CASE WHEN pool_status = 'temporal_review_hold'
                                    THEN 0 ELSE 1 END,
                                COALESCE(temporal_review_attempts, 0) ASC,
                                COALESCE(NULLIF(temporal_review_retry_at, ''),
                                         NULLIF(temporal_next_review_at, ''),
                                         last_scored_at) ASC,
                                bvid ASC
                            """,
                            (now_text, *guard_params),
                        ).fetchall()
                    ]
                    selected = self._exclude_viewed_rows(
                        rows,
                        self._recent_viewed_content_keys_on(connection),
                        limit=max_rows,
                    )[:max_rows]
                    leases: list[tuple[str, str]] = []
                    for row in selected:
                        if str(row.get("pool_status") or "fresh") != "temporal_review_hold":
                            continue
                        review_attempts = max(
                            0,
                            int(row.get("temporal_review_attempts", 0) or 0),
                        )
                        retry_hours = min(24, 2 ** min(review_attempts, 5))
                        retry_at = (
                            (effective_now + timedelta(hours=retry_hours))
                            .astimezone(UTC)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                        leases.append((retry_at, str(row["bvid"])))
                    if leases:
                        connection.executemany(
                            """
                            UPDATE content_cache
                            SET temporal_review_attempts =
                                    COALESCE(temporal_review_attempts, 0) + 1,
                                temporal_review_retry_at = ?
                            WHERE bvid = ?
                              AND pool_status = 'temporal_review_hold'
                            """,
                            leases,
                        )
                    connection.commit()
                    return selected
                except Exception:
                    connection.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                self._rollback_failed_write(self.conn)
                if "database is locked" not in str(exc).lower() or attempts <= 1:
                    raise
                attempts -= 1
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def get_pool_candidates_needing_copy(
        self,
        limit: int = 20,
        *,
        xhs_self_nickname: str = "",
        eligible_available_first: bool = False,
    ) -> list[dict[str, Any]]:
        """Return fresh pool candidates missing precomputed popup copy.

        v0.3.66+: requires ``style_key`` / ``topic_group`` — content must
        be classified before expression generation.  This prevents
        unclassified items (e.g. raw XHS notes) from getting an expression
        and leaking through the serve gate without proper relevance scoring.

        With ``eligible_available_first``, rows whose completed copy can add a
        slot to the public per-topic availability window are returned before
        deeper same-topic rows.  The complete admitted backlog remains
        selectable after that prefix, preserving copy-watermark fills.
        """
        self._ensure_fresh_read()
        max_rows = max(0, int(limit))
        if max_rows == 0:
            return []
        viewed_content_keys = self.get_recent_viewed_content_keys()
        pending_rows = self._load_admitted_pending_copy_rows_on(
            self.conn,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=viewed_content_keys,
        )
        if not eligible_available_first:
            return pending_rows[:max_rows]

        copy_ready_rows = self._load_available_pool_candidate_rows_on(
            self.conn,
            max_per_topic_group=0,
            xhs_self_nickname=xhs_self_nickname,
            _viewed_content_keys=viewed_content_keys,
        )
        eligible_rows = self._select_admitted_pending_available_rows(
            pending_rows,
            copy_ready_rows=copy_ready_rows,
        )
        eligible_ids = {str(row.get("bvid") or "") for row in eligible_rows}
        ordered = [
            *eligible_rows,
            *(row for row in pending_rows if str(row.get("bvid") or "") not in eligible_ids),
        ]
        return ordered[:max_rows]

    def update_pool_copy(
        self,
        bvid: str,
        *,
        expression: str,
        topic_label: str,
    ) -> None:
        """Persist precomputed popup copy for one pooled candidate."""
        if _looks_like_serialized_llm_payload(expression):
            # Sink defense only — the upstream validators are what actually
            # keep repr'd payloads out. This exists so a future parsing
            # regression cannot silently reach users as card copy again.
            logger.error("Refusing to persist serialized LLM payload as pool copy for %s", bvid)
            return
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_expression = ?,
                pool_topic_label = ?
            WHERE bvid = ?
            """,
            (expression, topic_label, bvid),
        )

    def get_latest_event_id(self) -> int:
        """Return the latest event primary key."""
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS latest_id FROM events")
        row = cursor.fetchone()
        return int(row["latest_id"]) if row is not None else 0

    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]:
        """Query events newer than a given id for selected event types."""
        if not event_types:
            return []
        placeholders = ", ".join("?" for _ in event_types)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM events
            WHERE id > ? AND event_type IN ({placeholders})
            ORDER BY id ASC
            """,
            [after_event_id, *event_types],
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_event_rows_after(
        self,
        *,
        after_event_id: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Scan the append-only event ledger in primary-key order.

        Continuous consumers must advance across rows they do not own, so this
        deliberately has no event-type filter.
        """
        cursor = self.conn.execute(
            """
            SELECT *
            FROM events
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (max(0, int(after_event_id)), max(1, int(limit))),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_event_rows_by_ids(self, event_ids: Sequence[int]) -> list[dict[str, Any]]:
        """Return durable event payloads for idempotent projection repair."""
        normalized = sorted({max(0, int(event_id)) for event_id in event_ids if int(event_id) > 0})
        if not normalized:
            return []
        placeholders = ", ".join("?" for _ in normalized)
        cursor = self.conn.execute(
            f"SELECT * FROM events WHERE id IN ({placeholders}) ORDER BY id ASC",
            normalized,
        )
        return [dict(row) for row in cursor.fetchall()]

    def insert_recommendation(
        self,
        bvid: str,
        *,
        item_key: str = "",
        confidence: float,
        expression: str = "",
        topic: str = "",
        presented: int = 0,
    ) -> int:
        """Insert a recommendation history record."""
        cursor = self._execute_write(
            """
            INSERT INTO recommendations
                (bvid, item_key, expression, topic, confidence, presented)
            VALUES (
                ?,
                COALESCE(
                    NULLIF(?, ''),
                    (SELECT item_key FROM content_cache WHERE bvid = ?),
                    ?
                ),
                ?, ?, ?, ?
            )
            """,
            (
                bvid,
                item_key.strip(),
                bvid,
                self._fallback_recommendation_item_key(bvid),
                expression,
                topic,
                confidence,
                presented,
            ),
        )
        return cursor.lastrowid or 0

    @staticmethod
    def _fallback_recommendation_item_key(bvid: str) -> str:
        """Build a canonical fallback when no cache identity row is available."""
        storage_key = bvid.strip()
        if ":" in storage_key:
            return storage_key
        return make_item_key("bilibili", storage_key)

    def batch_insert_recommendations(
        self,
        items: list[dict[str, Any]],
    ) -> list[int]:
        """Insert N recommendation rows in one transaction; return row IDs in order.

        Single fsync replaces N (was 200-300ms each under discovery write
        contention → ~3s for the popup's 10-item batch). Returns
        ``lastrowid`` per item, computed from the auto-increment delta
        since this connection's last id.
        """
        return self.batch_insert_recommendations_and_mark_shown(items, [])

    def batch_insert_eligible_recommendations_and_mark_shown(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
        *,
        now: datetime | None = None,
    ) -> PoolServePersistResult:
        """Commit only candidates still fresh and temporally eligible.

        This is the final recommendation eligibility boundary.  It re-reads
        temporal metadata under the same ``BEGIN IMMEDIATE`` that inserts
        history and marks rows shown, closing the snapshot-to-commit race in
        which an item could expire or be consumed by another process.
        """

        if not items:
            return PoolServePersistResult(
                recommendation_ids=(),
                committed_bvids=(),
            )
        explicit_now = now
        if explicit_now is not None and explicit_now.tzinfo is None:
            explicit_now = explicit_now.replace(tzinfo=UTC)
        requested_shown = {str(value) for value in shown_bvids if str(value)}
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    # Production calls take their clock only after acquiring
                    # the write lock, so waiting for another writer cannot
                    # carry a candidate across its TTL on a stale timestamp.
                    # Tests may inject ``now`` for deterministic boundaries.
                    effective_now = explicit_now or datetime.now(UTC)
                    ids: list[int] = []
                    committed_bvids: list[str] = []
                    temporally_stale_bvids: list[str] = []
                    skipped_bvids: list[str] = []
                    processed_bvids: set[str] = set()
                    for item in items:
                        bvid = str(item.get("bvid", "")).strip()
                        if not bvid or bvid in processed_bvids:
                            continue
                        if requested_shown and bvid not in requested_shown:
                            continue
                        processed_bvids.add(bvid)
                        row = cursor.execute(
                            """
                            SELECT bvid, pool_status, published_at,
                                   temporal_class, temporal_confidence,
                                   temporal_policy_version, temporal_validity_mode,
                                   temporal_valid_until, temporal_scope,
                                   temporal_state, temporal_evidence,
                                   temporal_next_review_at,
                                   temporal_evaluated_at,
                                   temporal_evidence_complete
                            FROM content_cache
                            WHERE bvid = ?
                            LIMIT 1
                            """,
                            (bvid,),
                        ).fetchone()
                        if row is None or str(row["pool_status"] or "fresh") != "fresh":
                            skipped_bvids.append(bvid)
                            continue
                        temporal = self._temporal_decision_for_row(
                            dict(row),
                            now=effective_now,
                        )
                        disposition = _temporal_decision_disposition(temporal)
                        if disposition != "eligible":
                            target_status = (
                                "stale" if disposition == "expired" else "temporal_review_hold"
                            )
                            cursor.execute(
                                """
                                UPDATE content_cache
                                SET pool_status = ?
                                WHERE bvid = ?
                                  AND COALESCE(pool_status, 'fresh') = 'fresh'
                                """,
                                (target_status, bvid),
                            )
                            if disposition == "expired":
                                temporally_stale_bvids.append(bvid)
                            else:
                                skipped_bvids.append(bvid)
                            continue
                        already_recommended = cursor.execute(
                            "SELECT 1 FROM recommendations WHERE bvid = ? LIMIT 1",
                            (bvid,),
                        ).fetchone()
                        if already_recommended is not None:
                            skipped_bvids.append(bvid)
                            continue
                        cursor.execute(
                            """
                            INSERT INTO recommendations
                                (bvid, item_key, expression, topic, confidence, presented)
                            VALUES (
                                ?,
                                COALESCE(
                                    NULLIF(?, ''),
                                    (SELECT item_key FROM content_cache WHERE bvid = ?),
                                    ?
                                ),
                                ?, ?, ?, ?
                            )
                            """,
                            (
                                bvid,
                                str(item.get("item_key", "")).strip(),
                                bvid,
                                self._fallback_recommendation_item_key(bvid),
                                str(item.get("expression", "")),
                                str(item.get("topic", "")),
                                float(item.get("confidence", 0.0) or 0.0),
                                int(item.get("presented", 0) or 0),
                            ),
                        )
                        ids.append(cursor.lastrowid or 0)
                        committed_bvids.append(bvid)
                    if committed_bvids:
                        placeholders = ", ".join("?" for _ in committed_bvids)
                        cursor.execute(
                            f"""
                            UPDATE content_cache
                            SET pool_status = 'shown',
                                recommended_at = CURRENT_TIMESTAMP
                            WHERE bvid IN ({placeholders})
                              AND COALESCE(pool_status, 'fresh') = 'fresh'
                            """,
                            committed_bvids,
                        )
                    self.conn.commit()
                    return PoolServePersistResult(
                        recommendation_ids=tuple(ids),
                        committed_bvids=tuple(committed_bvids),
                        temporally_stale_bvids=tuple(temporally_stale_bvids),
                        skipped_bvids=tuple(skipped_bvids),
                    )
                except Exception:
                    self.conn.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                self._rollback_failed_write(self.conn)
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def batch_insert_recommendations_and_mark_shown(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> list[int]:
        """Insert recommendations + mark pool items shown in **one transaction**.

        v0.3.45+: serve() used to fire two separate writes (insert recs,
        then UPDATE content_cache.pool_status='shown') and pay two
        fsyncs. Under refresh-tick write contention this stretched the
        tail to ~1s. One BEGIN IMMEDIATE / COMMIT pair gives the same
        atomic semantics with a single fsync, and the rare lost-write
        case (insert succeeds, mark fails) is now structurally
        impossible — both succeed or both rollback together.

        Returns ``lastrowid`` per item, in the same order as ``items``.
        """
        if not items and not shown_bvids:
            return []
        clean_bvids = [b for b in shown_bvids if b]
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    ids: list[int] = []
                    for item in items:
                        cursor.execute(
                            """
                            INSERT INTO recommendations
                                (bvid, item_key, expression, topic, confidence, presented)
                            VALUES (
                                ?,
                                COALESCE(
                                    NULLIF(?, ''),
                                    (SELECT item_key FROM content_cache WHERE bvid = ?),
                                    ?
                                ),
                                ?, ?, ?, ?
                            )
                            """,
                            (
                                str(item.get("bvid", "")),
                                str(item.get("item_key", "")).strip(),
                                str(item.get("bvid", "")),
                                self._fallback_recommendation_item_key(str(item.get("bvid", ""))),
                                str(item.get("expression", "")),
                                str(item.get("topic", "")),
                                float(item.get("confidence", 0.0) or 0.0),
                                int(item.get("presented", 0) or 0),
                            ),
                        )
                        ids.append(cursor.lastrowid or 0)
                    if clean_bvids:
                        placeholders = ", ".join("?" for _ in clean_bvids)
                        cursor.execute(
                            f"""
                            UPDATE content_cache
                            SET pool_status = 'shown',
                                recommended_at = CURRENT_TIMESTAMP
                            WHERE bvid IN ({placeholders})
                            """,
                            clean_bvids,
                        )
                    self.conn.commit()
                    return ids
                except Exception:
                    self.conn.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempts <= 1:
                    raise
                attempts -= 1
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def get_recent_recommendation_signals(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent recommendations with topic/source for scoring context.

        Includes both ``topic_key`` (fine, e.g. ``"洛克王国"``) and
        ``topic_group`` (coarse, e.g. ``"游戏"``) so the curator can fatigue
        on both axes. Without ``topic_group``, sibling fine-grained keys
        like ``动漫杂谈`` / ``动漫补番`` / ``动漫解说`` are independent and
        per-key fatigue never fires across them.
        """
        cursor = self.conn.execute(
            """
            SELECT r.bvid, c.topic_key, c.topic_group, c.source, r.created_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_recommendation_signals_since(
        self,
        *,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Return recommendation topic/source rows shown since a timestamp."""
        self._ensure_fresh_read()
        since_text = since.isoformat(sep=" ")
        cursor = self.conn.execute(
            """
            SELECT r.bvid,
                   c.topic_key,
                   c.topic_group,
                   c.source,
                   r.created_at,
                   r.presented_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE COALESCE(r.presented_at, r.created_at) >= ?
            ORDER BY COALESCE(r.presented_at, r.created_at) DESC, r.id DESC
            """,
            (since_text,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_feedback_signals(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent feedback with UP/topic/franchise info for score
        adjustment.

        ``franchise_key`` is the LLM-tagged IP / series column (added in
        v0.3.18). Disliking one 原神 video used to only block its exact
        bvid; now the curator collects ``franchise_key`` across recent
        dislikes and down-ranks any candidate whose own ``franchise_key``
        matches — without relying on title-string heuristics.
        """
        cursor = self.conn.execute(
            """
            SELECT r.feedback_type, c.up_mid, c.up_name, c.topic_key,
                   c.topic_group, c.source, c.title, c.franchise_key
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.feedback_type IS NOT NULL
            ORDER BY r.feedback_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recommendations(
        self,
        limit: int = 100,
        *,
        exclude_processed: bool = False,
    ) -> list[dict[str, Any]]:
        """Get recommendation history ordered by newest first.

        xhs rows whose cached ``content_url`` is missing ``xsec_token``
        are filtered out — clicking them hits xhs's 300031 login wall.

        When *exclude_processed* is True, rows that have already been
        acted upon (liked / disliked / dismissed / commented) are
        omitted so the API only returns actionable items.

        ``franchise_key`` (v0.3.18) is exposed so /api/recommendations
        can apply a final per-IP cap before returning to the client —
        otherwise five 原神 / 提瓦特 items can land in one popup view.
        """
        self._ensure_fresh_read()
        admission_sql, admission_params = self._pool_admission_sql(
            score_expr="COALESCE(r.confidence, 0.0)",
            source_expr="c.source",
        )
        processed_clause = (
            "AND (r.feedback_type IS NULL OR r.feedback_type = '')" if exclude_processed else ""
        )
        # Actionable rows can age out after their recommendation history row
        # was created. Fetch the complete actionable set so temporal filtering
        # happens before the caller's limit; immutable history reads keep the
        # existing bounded SQL path and retain expired entries for audit.
        limit_clause = "" if exclude_processed else "LIMIT ?"
        query_params: tuple[Any, ...] = (
            (*admission_params,) if exclude_processed else (*admission_params, limit)
        )
        cursor = self.conn.execute(
            f"""
            SELECT
                r.id,
                r.bvid,
                COALESCE(NULLIF(r.item_key, ''), c.item_key, '') AS item_key,
                r.expression,
                r.topic,
                r.confidence,
                r.presented,
                r.feedback,
                r.feedback_type,
                r.feedback_note,
                r.created_at,
                r.presented_at,
                r.feedback_at,
                COALESCE(c.title, '') AS title,
                COALESCE(c.up_name, '') AS up_name,
                COALESCE(c.cover_url, '') AS cover_url,
                COALESCE(c.content_id, r.bvid) AS content_id,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform,
                COALESCE(c.content_type, 'video') AS content_type,
                COALESCE(c.description, '') AS description,
                COALESCE(c.body_text, '') AS body_text,
                COALESCE(c.tags, '[]') AS tags,
                COALESCE(c.topic_key, '') AS topic_key,
                COALESCE(c.topic_group, '') AS topic_group,
                COALESCE(c.pool_topic_label, '') AS pool_topic_label,
                COALESCE(c.published_at, '') AS published_at,
                COALESCE(c.published_label, '') AS published_label,
                COALESCE(c.temporal_class, 'unknown') AS temporal_class,
                COALESCE(c.temporal_confidence, 0.0) AS temporal_confidence,
                COALESCE(c.temporal_reason, '') AS temporal_reason,
                COALESCE(c.temporal_policy_version, 'v1') AS temporal_policy_version,
                COALESCE(c.temporal_validity_mode, 'none') AS temporal_validity_mode,
                COALESCE(c.temporal_valid_until, '') AS temporal_valid_until,
                COALESCE(c.temporal_scope, 'none') AS temporal_scope,
                COALESCE(c.temporal_state, 'unknown') AS temporal_state,
                COALESCE(c.temporal_evidence, '') AS temporal_evidence,
                COALESCE(c.temporal_next_review_at, '') AS temporal_next_review_at,
                COALESCE(c.temporal_evaluated_at, '') AS temporal_evaluated_at,
                COALESCE(c.temporal_evidence_complete, 0) AS temporal_evidence_complete,
                COALESCE(c.franchise_key, '') AS franchise_key,
                COALESCE(c.duration, 0) AS duration,
                COALESCE(c.view_count, 0) AS view_count,
                COALESCE(c.like_count, 0) AS like_count,
                COALESCE(c.danmaku_count, 0) AS danmaku_count,
                COALESCE(c.favorite_count, 0) AS favorite_count,
                COALESCE(c.comment_count, 0) AS comment_count,
                COALESCE(c.share_count, 0) AS share_count,
                COALESCE(c.rating_score, 0.0) AS rating_score,
                COALESCE(c.rating_count, 0) AS rating_count,
                COALESCE(c.source_rank, 0) AS source_rank,
                COALESCE(c.up_mid, 0) AS up_mid
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.item_key = COALESCE(
                NULLIF(r.item_key, ''),
                (SELECT item_key FROM content_cache WHERE bvid = r.bvid),
                (
                    SELECT CASE WHEN COUNT(*) = 1 THEN MIN(item_key) END
                    FROM content_cache
                    WHERE content_id = r.bvid
                )
            )
            WHERE (
                COALESCE(c.source_platform, '') != 'xiaohongshu'
                OR COALESCE(c.content_url, '') LIKE '%xsec_token=%'
            )
            AND {admission_sql}
            {processed_clause}
            ORDER BY r.created_at DESC, r.id DESC
            {limit_clause}
            """,
            query_params,
        )
        rows = [dict(row) for row in cursor.fetchall()]
        if not exclude_processed:
            return rows
        temporal_now = datetime.now(UTC)
        eligible = [
            row for row in rows if self._temporal_eligibility_for_row(row, now=temporal_now)
        ]
        return eligible[: max(0, int(limit))]

    def count_recommendations(self) -> int:
        """Return the total number of stored recommendations."""
        self._ensure_fresh_read()
        cursor = self.conn.execute("SELECT COUNT(*) AS count FROM recommendations")
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def count_unread_recommendations(self) -> int:
        """Return temporally eligible recommendations not yet presented."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT COALESCE(c.published_at, '') AS published_at,
                   COALESCE(c.temporal_class, 'unknown') AS temporal_class,
                   COALESCE(c.temporal_confidence, 0.0) AS temporal_confidence,
                   COALESCE(c.temporal_policy_version, 'v1') AS temporal_policy_version,
                   COALESCE(c.temporal_validity_mode, 'none') AS temporal_validity_mode,
                   COALESCE(c.temporal_valid_until, '') AS temporal_valid_until,
                   COALESCE(c.temporal_scope, 'none') AS temporal_scope,
                   COALESCE(c.temporal_state, 'unknown') AS temporal_state,
                   COALESCE(c.temporal_evidence, '') AS temporal_evidence,
                   COALESCE(c.temporal_next_review_at, '') AS temporal_next_review_at,
                   COALESCE(c.temporal_evaluated_at, '') AS temporal_evaluated_at,
                   COALESCE(c.temporal_evidence_complete, 0) AS temporal_evidence_complete
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.item_key = COALESCE(
                NULLIF(r.item_key, ''),
                (SELECT item_key FROM content_cache WHERE bvid = r.bvid),
                (
                    SELECT CASE WHEN COUNT(*) = 1 THEN MIN(item_key) END
                    FROM content_cache
                    WHERE content_id = r.bvid
                )
            )
            WHERE r.presented = 0
            """
        )
        temporal_now = datetime.now(UTC)
        return sum(
            1
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        )

    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None:
        """Return one recommendation worth notifying the user about."""
        cursor = self.conn.execute(
            """
            SELECT
                r.id,
                r.bvid,
                r.expression,
                r.confidence,
                r.topic,
                c.title,
                COALESCE(c.description, '') AS description,
                COALESCE(c.body_text, '') AS body_text,
                COALESCE(c.up_name, '') AS up_name,
                COALESCE(c.tags, '[]') AS tags,
                COALESCE(c.topic_key, '') AS topic_key,
                COALESCE(c.topic_group, '') AS topic_group,
                COALESCE(c.pool_topic_label, '') AS pool_topic_label,
                COALESCE(c.published_at, '') AS published_at,
                COALESCE(c.temporal_class, 'unknown') AS temporal_class,
                COALESCE(c.temporal_confidence, 0.0) AS temporal_confidence,
                COALESCE(c.temporal_policy_version, 'v1') AS temporal_policy_version,
                COALESCE(c.temporal_validity_mode, 'none') AS temporal_validity_mode,
                COALESCE(c.temporal_valid_until, '') AS temporal_valid_until,
                COALESCE(c.temporal_scope, 'none') AS temporal_scope,
                COALESCE(c.temporal_state, 'unknown') AS temporal_state,
                COALESCE(c.temporal_evidence, '') AS temporal_evidence,
                COALESCE(c.temporal_next_review_at, '') AS temporal_next_review_at,
                COALESCE(c.temporal_evaluated_at, '') AS temporal_evaluated_at,
                COALESCE(c.temporal_evidence_complete, 0) AS temporal_evidence_complete,
                c.notification_sent,
                c.notified_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.presented = 0
              AND c.notification_sent = 0
              AND r.confidence >= ?
            ORDER BY r.confidence DESC, r.created_at DESC, r.id DESC
            """,
            (min_confidence,),
        )
        temporal_now = datetime.now(UTC)
        return next(
            (
                dict(row)
                for row in cursor.fetchall()
                if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
            ),
            None,
        )

    def mark_notification_sent(self, bvid: str) -> None:
        """Mark one cached item as already notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET notification_sent = 1,
                notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_recommendation_content(
        self,
        recommendation_id: int,
        *,
        expression: str,
        topic: str,
    ) -> None:
        """Update the generated expression fields of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET expression = ?, topic = ?
            WHERE id = ?
            """,
            (expression, topic, recommendation_id),
        )

    def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, Any] | None:
        """Return a single recommendation row by primary key."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT
                r.*,
                r.topic AS topic_label,
                c.title AS title,
                c.up_name AS up_name,
                COALESCE(c.content_id, r.bvid) AS content_id,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.id = ?
            """,
            (recommendation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def update_recommendation_feedback(
        self,
        recommendation_id: int,
        *,
        feedback_type: str,
        feedback_note: str = "",
    ) -> None:
        """Update the current feedback state of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET feedback = ?,
                feedback_type = ?,
                feedback_note = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (feedback_type, feedback_type, feedback_note, recommendation_id),
        )
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'feedbacked',
                feedback_type = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE bvid = (
                SELECT bvid
                FROM recommendations
                WHERE id = ?
            )
            """,
            (feedback_type, recommendation_id),
        )

    def mark_recommendations_presented(self, recommendation_ids: list[int]) -> None:
        """Mark recommendations as presented and set their presented timestamp."""
        if not recommendation_ids:
            return
        placeholders = ", ".join("?" for _ in recommendation_ids)
        self._execute_write(
            f"""
            UPDATE recommendations
            SET presented = 1,
                presented_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            recommendation_ids,
        )

    def prune_content_history(
        self,
        *,
        retention_days: int = CONTENT_HISTORY_RETENTION_DAYS,
    ) -> int:
        """Delete saved-removal snapshots outside the bounded history window."""
        days = max(1, int(retention_days))
        cursor = self._execute_write(
            """
            DELETE FROM saved_item_removals
            WHERE removed_at < datetime('now', ?)
            """,
            (f"-{days} days",),
        )
        return max(0, int(cursor.rowcount or 0))

    def list_content_history(
        self,
        category: str,
        *,
        limit: int = 12,
        offset: int = 0,
        retention_days: int = CONTENT_HISTORY_RETENTION_DAYS,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return an offset-compatible page for legacy callers."""
        items, total, _has_more, _next_position = self.list_content_history_page(
            category,
            limit=limit,
            offset=offset,
            retention_days=retention_days,
        )
        return items, total

    def list_content_history_page(
        self,
        category: str,
        *,
        limit: int = 12,
        offset: int = 0,
        cursor: tuple[str, int, int, str, int, int, int] | None = None,
        retention_days: int = CONTENT_HISTORY_RETENTION_DAYS,
    ) -> tuple[
        list[dict[str, Any]],
        int,
        bool,
        tuple[str, int, int, str, int, int, int] | None,
    ]:
        """Return one page of the 30-day content-history projection.

        ``clicked`` is projected from recommendation-owned click events,
        ``shown`` from recommendation rows without a matching click, and
        ``removed`` from saved-list removal snapshots plus explicit card
        dismiss/dislike feedback.  Rows are deduplicated by canonical item key
        within each category so repeated exposure does not flood the UI.
        """
        normalized_category = str(category or "").strip().lower()
        if normalized_category not in _CONTENT_HISTORY_CATEGORIES:
            raise ValueError(f"unsupported content history category: {category!r}")
        page_limit = max(1, min(100, int(limit)))
        if cursor is not None:
            if offset:
                raise ValueError("content history cursor cannot be combined with offset")
            (
                occurred_at,
                source_kind,
                source_id,
                item_key,
                event_anchor,
                recommendation_anchor,
                removal_anchor,
            ) = cursor
            if (
                not isinstance(occurred_at, str)
                or not occurred_at
                or len(occurred_at) > 64
                or any(unicodedata.category(character).startswith("C") for character in occurred_at)
                or not isinstance(source_kind, int)
                or isinstance(source_kind, bool)
                or source_kind < 0
                or source_kind > 2
                or not isinstance(source_id, int)
                or isinstance(source_id, bool)
                or source_id < 0
                or source_id > (1 << 63) - 1
                or not isinstance(item_key, str)
                or not item_key
                or len(item_key) > 2048
                or any(character.isspace() for character in item_key)
                or any(unicodedata.category(character).startswith("C") for character in item_key)
                or any(
                    not isinstance(anchor, int)
                    or isinstance(anchor, bool)
                    or anchor < 0
                    or anchor > (1 << 63) - 1
                    for anchor in (event_anchor, recommendation_anchor, removal_anchor)
                )
            ):
                raise ValueError("invalid content history cursor position")
        # Python integers are unbounded, while SQLite LIMIT/OFFSET bindings are
        # signed 64-bit integers.  The HTTP boundary rejects larger values;
        # keep the storage API defensive for direct callers too.
        page_offset = max(0, min((1 << 63) - 1, int(offset)))
        days = max(1, min(365, int(retention_days)))
        cutoff = f"-{days} days"
        self._ensure_fresh_read()

        cursor_anchors: tuple[int | None, int | None, int | None] = (
            (None, None, None) if cursor is None else (cursor[4], cursor[5], cursor[6])
        )
        history_snapshot_cte = """
            history_snapshot AS MATERIALIZED (
                SELECT
                    COALESCE(
                        ?,
                        (SELECT COALESCE(MAX(id), 0) FROM events),
                        0
                    ) AS event_anchor,
                    COALESCE(
                        ?,
                        (SELECT COALESCE(MAX(id), 0) FROM recommendations),
                        0
                    ) AS recommendation_anchor,
                    COALESCE(
                        ?,
                        (SELECT COALESCE(MAX(id), 0) FROM saved_item_removals),
                        0
                    ) AS removal_anchor
            )
        """

        recent_clicks_cte = """
            recent_click_facts AS MATERIALIZED (
                SELECT
                    e.id AS source_id,
                    0 AS source_kind,
                    e.created_at AS occurred_at,
                    NULLIF(CAST(json_extract(e.metadata, '$.recommendation_id') AS INTEGER), 0)
                        AS raw_recommendation_id,
                    CASE
                        WHEN json_type(e.metadata, '$.source_platform') IS NULL
                          OR json_type(e.metadata, '$.source_platform') = 'null'
                          OR (
                              json_type(e.metadata, '$.source_platform') = 'text'
                              AND trim(json_extract(
                                  e.metadata, '$.source_platform'
                              )) = ''
                          ) THEN 'bilibili'
                        WHEN json_type(e.metadata, '$.source_platform') != 'text'
                            THEN ''
                        ELSE normalize_source_platform(
                            json_extract(e.metadata, '$.source_platform')
                        )
                    END AS source_platform,
                    COALESCE(
                        NULLIF(json_extract(e.metadata, '$.content_id'), ''),
                        NULLIF(json_extract(e.metadata, '$.bvid'), ''),
                        ''
                    ) AS content_id,
                    COALESCE(
                        NULLIF(json_extract(e.metadata, '$.content_url'), ''),
                        NULLIF(e.url, ''),
                        ''
                    ) AS content_url,
                    COALESCE(NULLIF(e.title, ''), '') AS event_title,
                    COALESCE(
                        NULLIF(json_extract(e.metadata, '$.up_name'), ''),
                        NULLIF(json_extract(e.metadata, '$.author'), ''),
                        ''
                    ) AS event_author
                FROM events AS e
                CROSS JOIN history_snapshot AS snapshot
                WHERE e.event_type = 'click'
                  AND e.id <= snapshot.event_anchor
                  AND e.created_at >= datetime('now', ?)
                  AND json_valid(e.metadata)
                  AND (
                      json_extract(e.metadata, '$.source') = 'recommendation_click'
                      OR json_extract(e.metadata, '$.event_namespace') = 'recommendation'
                  )
            ),
            recent_clicks AS MATERIALIZED (
                SELECT
                    facts.source_id,
                    facts.source_kind,
                    facts.occurred_at,
                    COALESCE(r.id, facts.raw_recommendation_id) AS recommendation_id,
                    COALESCE(
                        NULLIF(normalize_content_history_item_key(r.item_key), ''),
                        NULLIF((
                            SELECT normalize_content_history_item_key(cc.item_key)
                            FROM content_cache AS cc
                            WHERE normalize_source_platform(cc.source_platform) =
                                    facts.source_platform
                              AND facts.source_platform != ''
                              AND cc.content_id = facts.content_id
                              AND cc.item_key != ''
                            ORDER BY cc.last_scored_at DESC, cc.bvid DESC
                            LIMIT 1
                        ), ''),
                        NULLIF(normalize_content_history_item_key(r.bvid), ''),
                        NULLIF(normalize_content_history_item_key(
                            facts.source_platform || ':' || r.bvid
                        ), ''),
                        NULLIF(normalize_content_history_item_key(
                            facts.source_platform || ':' || facts.content_id
                        ), ''),
                        COALESCE(
                            NULLIF(facts.source_platform, ''),
                            'unknown'
                        ) || ':event-' || facts.source_id
                    ) AS resolved_item_key,
                    COALESCE(
                        (
                            SELECT cc.bvid
                            FROM content_cache AS cc
                            WHERE cc.item_key = r.item_key
                            ORDER BY cc.last_scored_at DESC, cc.bvid DESC
                            LIMIT 1
                        ),
                        (
                            SELECT cc.bvid
                            FROM content_cache AS cc
                            WHERE cc.bvid = r.bvid
                            LIMIT 1
                        ),
                        (
                            SELECT cc.bvid
                            FROM content_cache AS cc
                            WHERE normalize_source_platform(cc.source_platform) =
                                    facts.source_platform
                              AND facts.source_platform != ''
                              AND cc.content_id = facts.content_id
                            ORDER BY cc.last_scored_at DESC, cc.bvid DESC
                            LIMIT 1
                        )
                    ) AS hydration_bvid,
                    COALESCE(
                        NULLIF(content_history_item_key_content_id(
                            normalize_content_history_item_key(r.item_key)
                        ), ''),
                        NULLIF(content_history_item_key_content_id(
                            normalize_content_history_item_key(r.bvid)
                        ), ''),
                        CASE WHEN instr(r.bvid, ':') = 0 THEN r.bvid ELSE '' END,
                        ''
                    ) AS recommendation_content_id,
                    facts.source_platform,
                    facts.content_id,
                    facts.content_url,
                    facts.event_title,
                    facts.event_author
                FROM recent_click_facts AS facts
                LEFT JOIN recommendations AS r ON r.id = facts.raw_recommendation_id
            )
        """

        if normalized_category == "clicked":
            cte = (
                "WITH "
                + history_snapshot_cte
                + ","
                + recent_clicks_cte
                + """,
                hydrated AS (
                    SELECT
                        recent_clicks.source_id,
                        recent_clicks.source_kind,
                        recent_clicks.occurred_at,
                        recent_clicks.recommendation_id,
                        recent_clicks.resolved_item_key AS item_key,
                        COALESCE(
                            NULLIF(normalize_source_platform(c.source_platform), ''),
                            NULLIF(content_history_item_key_platform(
                                recent_clicks.resolved_item_key
                            ), ''),
                            recent_clicks.source_platform
                        ) AS source_platform,
                        COALESCE(
                            NULLIF(c.content_id, ''),
                            NULLIF(recent_clicks.content_id, ''),
                            NULLIF(recent_clicks.recommendation_content_id, ''),
                            ''
                        ) AS content_id,
                        COALESCE(
                            NULLIF(c.content_url, ''),
                            NULLIF(recent_clicks.content_url, ''),
                            ''
                        ) AS content_url,
                        COALESCE(NULLIF(c.content_type, ''), 'video') AS content_type,
                        COALESCE(NULLIF(c.title, ''), NULLIF(recent_clicks.event_title, ''), '')
                            AS title,
                        COALESCE(NULLIF(c.up_name, ''), NULLIF(c.author_name, ''),
                                 NULLIF(recent_clicks.event_author, ''), '') AS author_name,
                        COALESCE(c.cover_url, '') AS cover_url,
                        COALESCE(c.body_text, '') AS body_text,
                        '' AS context,
                        0 AS restored,
                        '[]' AS contexts_json
                    FROM recent_clicks
                    LEFT JOIN content_cache AS c
                      ON c.bvid = recent_clicks.hydration_bvid
                ),
                ranked AS (
                    SELECT hydrated.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY item_key
                               ORDER BY occurred_at DESC, source_kind DESC, source_id DESC
                           ) AS row_number
                    FROM hydrated
                ),
                entries AS MATERIALIZED (
                    SELECT * FROM ranked WHERE row_number = 1
                )
            """
            )
            params: tuple[Any, ...] = (*cursor_anchors, cutoff)
        elif normalized_category == "shown":
            cte = (
                "WITH "
                + history_snapshot_cte
                + ","
                + recent_clicks_cte
                + """,
                clicked_recommendation_ids AS MATERIALIZED (
                    SELECT DISTINCT recommendation_id
                    FROM recent_clicks
                    WHERE recommendation_id IS NOT NULL
                ),
                clicked_item_keys AS MATERIALIZED (
                    SELECT DISTINCT resolved_item_key
                    FROM recent_clicks
                ),
                eligible AS MATERIALIZED (
                    SELECT
                        r.id AS source_id,
                        0 AS source_kind,
                        COALESCE(r.presented_at, r.created_at) AS occurred_at,
                        r.id AS recommendation_id,
                        COALESCE(
                            NULLIF(normalize_content_history_item_key(r.item_key), ''),
                            NULLIF((
                                SELECT normalize_content_history_item_key(cc.item_key)
                                FROM content_cache AS cc
                                WHERE cc.bvid = r.bvid
                                LIMIT 1
                            ), ''),
                            NULLIF((
                                SELECT normalize_content_history_item_key(cc.item_key)
                                FROM content_cache AS cc
                                WHERE cc.content_id = r.bvid
                                  AND cc.item_key != ''
                                ORDER BY
                                    CASE
                                        WHEN normalize_source_platform(
                                            cc.source_platform
                                        ) = 'bilibili' THEN 0
                                        ELSE 1
                                    END,
                                    cc.last_scored_at DESC,
                                    cc.bvid DESC
                                LIMIT 1
                            ), ''),
                            NULLIF(normalize_content_history_item_key(r.bvid), ''),
                            NULLIF(normalize_content_history_item_key(
                                'bilibili:' || r.bvid
                            ), ''),
                            'bilibili:rec-' || r.id
                        ) AS resolved_item_key,
                        COALESCE(
                            (
                                SELECT cc.bvid
                                FROM content_cache AS cc
                                WHERE cc.item_key = r.item_key
                                ORDER BY cc.last_scored_at DESC, cc.bvid DESC
                                LIMIT 1
                            ),
                            (
                                SELECT cc.bvid
                                FROM content_cache AS cc
                                WHERE cc.bvid = r.bvid
                                LIMIT 1
                            ),
                            (
                                SELECT cc.bvid
                                FROM content_cache AS cc
                                WHERE cc.content_id = r.bvid
                                ORDER BY
                                    CASE
                                        WHEN normalize_source_platform(
                                            cc.source_platform
                                        ) = 'bilibili' THEN 0
                                        ELSE 1
                                    END,
                                    cc.last_scored_at DESC,
                                    cc.bvid DESC
                                LIMIT 1
                            )
                        ) AS hydration_bvid,
                        COALESCE(
                            NULLIF(content_history_item_key_content_id(
                                normalize_content_history_item_key(r.item_key)
                            ), ''),
                            NULLIF(content_history_item_key_content_id(
                                normalize_content_history_item_key(r.bvid)
                            ), ''),
                            CASE WHEN instr(r.bvid, ':') = 0 THEN r.bvid ELSE '' END,
                            ''
                        ) AS fallback_content_id,
                        r.bvid
                    FROM recommendations AS r
                    CROSS JOIN history_snapshot AS snapshot
                    WHERE COALESCE(r.presented_at, r.created_at) >= datetime('now', ?)
                      AND r.id <= snapshot.recommendation_anchor
                      AND COALESCE(r.feedback_type, '') NOT IN ('dismiss', 'dislike')
                ),
                hydrated AS (
                    SELECT
                        eligible.source_id,
                        eligible.source_kind,
                        eligible.occurred_at,
                        eligible.recommendation_id,
                        eligible.resolved_item_key AS item_key,
                        COALESCE(
                            NULLIF(normalize_source_platform(c.source_platform), ''),
                            NULLIF(content_history_item_key_platform(
                                eligible.resolved_item_key
                            ), ''),
                            'bilibili'
                        ) AS source_platform,
                        COALESCE(
                            NULLIF(c.content_id, ''),
                            NULLIF(eligible.fallback_content_id, ''),
                            ''
                        ) AS content_id,
                        COALESCE(c.content_url, '') AS content_url,
                        COALESCE(NULLIF(c.content_type, ''), 'video') AS content_type,
                        COALESCE(c.title, '') AS title,
                        COALESCE(NULLIF(c.up_name, ''), NULLIF(c.author_name, ''), '')
                            AS author_name,
                        COALESCE(c.cover_url, '') AS cover_url,
                        COALESCE(c.body_text, '') AS body_text,
                        '' AS context,
                        0 AS restored,
                        '[]' AS contexts_json
                    FROM eligible
                    LEFT JOIN content_cache AS c ON c.bvid = eligible.hydration_bvid
                    WHERE eligible.source_id NOT IN (
                              SELECT recommendation_id
                              FROM clicked_recommendation_ids
                          )
                      AND eligible.resolved_item_key NOT IN (
                              SELECT resolved_item_key
                              FROM clicked_item_keys
                          )
                ),
                ranked AS (
                    SELECT hydrated.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY item_key
                               ORDER BY occurred_at DESC, source_kind DESC, source_id DESC
                           ) AS row_number
                    FROM hydrated
                ),
                entries AS MATERIALIZED (
                    SELECT * FROM ranked WHERE row_number = 1
                )
            """
            )
            params = (*cursor_anchors, cutoff, cutoff)
        else:
            cte = (
                "WITH "
                + history_snapshot_cte
                + """,
                normalized_memberships AS MATERIALIZED (
                    SELECT
                        list_kind,
                        normalize_content_history_item_key(item_key) AS canonical_item_key
                    FROM saved_memberships
                ),
                recent_saved_facts AS MATERIALIZED (
                    SELECT
                        COALESCE(
                            NULLIF(normalize_content_history_item_key(removal.item_key), ''),
                            NULLIF(normalize_content_history_item_key(
                                normalize_source_platform(removal.source_platform)
                                || ':' || removal.content_id
                            ), ''),
                            ''
                        ) AS canonical_item_key,
                        removal.*
                    FROM saved_item_removals AS removal
                    CROSS JOIN history_snapshot AS snapshot
                    WHERE removal.removed_at >= datetime('now', ?)
                      AND removal.id <= snapshot.removal_anchor
                ),
                recent_saved AS (
                    SELECT
                        removal.id AS source_id,
                        2 AS source_kind,
                        removal.removed_at AS occurred_at,
                        NULL AS recommendation_id,
                        COALESCE(
                            NULLIF(removal.canonical_item_key, ''),
                            'bilibili:removal-' || removal.id
                        ) AS item_key,
                        COALESCE(
                            NULLIF(normalize_source_platform(removal.source_platform), ''),
                            NULLIF(content_history_item_key_platform(
                                removal.canonical_item_key
                            ), ''),
                            'bilibili'
                        ) AS source_platform,
                        removal.content_id,
                        removal.content_url,
                        removal.content_type,
                        removal.title,
                        removal.author_name,
                        removal.cover_url,
                        '' AS body_text,
                        removal.list_kind AS context,
                        CASE WHEN EXISTS (
                            SELECT 1
                            FROM normalized_memberships AS membership
                            WHERE membership.list_kind = removal.list_kind
                              AND removal.canonical_item_key != ''
                              AND membership.canonical_item_key != ''
                              AND membership.canonical_item_key =
                                  removal.canonical_item_key
                        ) THEN 1 ELSE 0 END AS restored
                    FROM recent_saved_facts AS removal
                ),
                eligible_feedback AS MATERIALIZED (
                    SELECT
                        r.id AS source_id,
                        1 AS source_kind,
                        COALESCE(r.feedback_at, r.created_at) AS occurred_at,
                        r.id AS recommendation_id,
                        COALESCE(
                            NULLIF(normalize_content_history_item_key(r.item_key), ''),
                            NULLIF((
                                SELECT normalize_content_history_item_key(cc.item_key)
                                FROM content_cache AS cc
                                WHERE cc.bvid = r.bvid
                                LIMIT 1
                            ), ''),
                            NULLIF((
                                SELECT normalize_content_history_item_key(cc.item_key)
                                FROM content_cache AS cc
                                WHERE cc.content_id = r.bvid
                                  AND cc.item_key != ''
                                ORDER BY
                                    CASE
                                        WHEN normalize_source_platform(
                                            cc.source_platform
                                        ) = 'bilibili' THEN 0
                                        ELSE 1
                                    END,
                                    cc.last_scored_at DESC,
                                    cc.bvid DESC
                                LIMIT 1
                            ), ''),
                            NULLIF(normalize_content_history_item_key(r.bvid), ''),
                            NULLIF(normalize_content_history_item_key(
                                'bilibili:' || r.bvid
                            ), ''),
                            'bilibili:rec-' || r.id
                        ) AS resolved_item_key,
                        COALESCE(
                            (
                                SELECT cc.bvid
                                FROM content_cache AS cc
                                WHERE cc.item_key = r.item_key
                                ORDER BY cc.last_scored_at DESC, cc.bvid DESC
                                LIMIT 1
                            ),
                            (
                                SELECT cc.bvid
                                FROM content_cache AS cc
                                WHERE cc.bvid = r.bvid
                                LIMIT 1
                            ),
                            (
                                SELECT cc.bvid
                                FROM content_cache AS cc
                                WHERE cc.content_id = r.bvid
                                ORDER BY
                                    CASE
                                        WHEN normalize_source_platform(
                                            cc.source_platform
                                        ) = 'bilibili' THEN 0
                                        ELSE 1
                                    END,
                                    cc.last_scored_at DESC,
                                    cc.bvid DESC
                                LIMIT 1
                            )
                        ) AS hydration_bvid,
                        COALESCE(
                            NULLIF(content_history_item_key_content_id(
                                normalize_content_history_item_key(r.item_key)
                            ), ''),
                            NULLIF(content_history_item_key_content_id(
                                normalize_content_history_item_key(r.bvid)
                            ), ''),
                            CASE WHEN instr(r.bvid, ':') = 0 THEN r.bvid ELSE '' END,
                            ''
                        ) AS fallback_content_id,
                        r.bvid,
                        COALESCE(r.feedback_type, 'dismiss') AS context
                    FROM recommendations AS r
                    CROSS JOIN history_snapshot AS snapshot
                    WHERE r.feedback_type IN ('dismiss', 'dislike')
                      AND COALESCE(r.feedback_at, r.created_at) >= datetime('now', ?)
                      AND r.id <= snapshot.recommendation_anchor
                ),
                recent_feedback AS (
                    SELECT
                        eligible_feedback.source_id,
                        eligible_feedback.source_kind,
                        eligible_feedback.occurred_at,
                        eligible_feedback.recommendation_id,
                        eligible_feedback.resolved_item_key AS item_key,
                        COALESCE(
                            NULLIF(normalize_source_platform(c.source_platform), ''),
                            NULLIF(content_history_item_key_platform(
                                eligible_feedback.resolved_item_key
                            ), ''),
                            'bilibili'
                        ) AS source_platform,
                        COALESCE(
                            NULLIF(c.content_id, ''),
                            NULLIF(eligible_feedback.fallback_content_id, ''),
                            ''
                        ) AS content_id,
                        COALESCE(c.content_url, '') AS content_url,
                        COALESCE(NULLIF(c.content_type, ''), 'video') AS content_type,
                        COALESCE(c.title, '') AS title,
                        COALESCE(NULLIF(c.up_name, ''), NULLIF(c.author_name, ''), '')
                            AS author_name,
                        COALESCE(c.cover_url, '') AS cover_url,
                        COALESCE(c.body_text, '') AS body_text,
                        eligible_feedback.context,
                        0 AS restored
                    FROM eligible_feedback
                    LEFT JOIN content_cache AS c
                      ON c.bvid = eligible_feedback.hydration_bvid
                ),
                hydrated AS (
                    SELECT * FROM recent_saved
                    UNION ALL
                    SELECT * FROM recent_feedback
                ),
                context_ranked AS (
                    SELECT hydrated.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY item_key, context
                               ORDER BY occurred_at DESC, source_kind DESC, source_id DESC
                           ) AS context_row_number
                    FROM hydrated
                ),
                latest_contexts AS MATERIALIZED (
                    SELECT *
                    FROM context_ranked
                    WHERE context_row_number = 1
                ),
                card_ranked AS (
                    SELECT latest_contexts.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY item_key
                               ORDER BY
                                   occurred_at DESC,
                                   source_kind DESC,
                                   source_id DESC,
                                   context ASC
                           ) AS card_row_number
                    FROM latest_contexts
                ),
                context_summaries AS MATERIALIZED (
                    SELECT
                        item_key,
                        MAX(CASE WHEN context = 'favorite' THEN occurred_at END)
                            AS favorite_occurred_at,
                        MAX(CASE WHEN context = 'favorite' THEN restored ELSE 0 END)
                            AS favorite_restored,
                        MAX(CASE WHEN context = 'favorite' THEN source_id ELSE 0 END)
                            AS favorite_source_id,
                        MAX(CASE WHEN context = 'watch_later' THEN occurred_at END)
                            AS watch_later_occurred_at,
                        MAX(CASE WHEN context = 'watch_later' THEN restored ELSE 0 END)
                            AS watch_later_restored,
                        MAX(CASE WHEN context = 'watch_later' THEN source_id ELSE 0 END)
                            AS watch_later_source_id,
                        MAX(CASE WHEN context = 'dismiss' THEN occurred_at END)
                            AS dismiss_occurred_at,
                        MAX(CASE WHEN context = 'dismiss' THEN source_id ELSE 0 END)
                            AS dismiss_source_id,
                        MAX(CASE WHEN context = 'dislike' THEN occurred_at END)
                            AS dislike_occurred_at,
                        MAX(CASE WHEN context = 'dislike' THEN source_id ELSE 0 END)
                            AS dislike_source_id
                    FROM latest_contexts
                    GROUP BY item_key
                ),
                entries AS MATERIALIZED (
                    SELECT
                        card_ranked.source_id,
                        card_ranked.source_kind,
                        card_ranked.occurred_at,
                        card_ranked.recommendation_id,
                        card_ranked.item_key,
                        card_ranked.source_platform,
                        card_ranked.content_id,
                        card_ranked.content_url,
                        card_ranked.content_type,
                        card_ranked.title,
                        card_ranked.author_name,
                        card_ranked.cover_url,
                        card_ranked.body_text,
                        card_ranked.context,
                        card_ranked.restored,
                        context_summaries.favorite_occurred_at,
                        context_summaries.favorite_restored,
                        context_summaries.favorite_source_id,
                        context_summaries.watch_later_occurred_at,
                        context_summaries.watch_later_restored,
                        context_summaries.watch_later_source_id,
                        context_summaries.dismiss_occurred_at,
                        context_summaries.dismiss_source_id,
                        context_summaries.dislike_occurred_at,
                        context_summaries.dislike_source_id
                    FROM card_ranked
                    JOIN context_summaries USING (item_key)
                    WHERE card_ranked.card_row_number = 1
                )
            """
            )
            params = (*cursor_anchors, cutoff, cutoff)

        cursor_clause = ""
        cursor_params: tuple[Any, ...] = ()
        if cursor is not None:
            occurred_at, source_kind, source_id, item_key = cursor[:4]
            cursor_clause = """
                WHERE
                    occurred_at < ?
                    OR (occurred_at = ? AND source_kind < ?)
                    OR (
                        occurred_at = ?
                        AND source_kind = ?
                        AND source_id < ?
                    )
                    OR (
                        occurred_at = ?
                        AND source_kind = ?
                        AND source_id = ?
                        AND item_key > ?
                    )
            """
            cursor_params = (
                occurred_at,
                occurred_at,
                source_kind,
                occurred_at,
                source_kind,
                source_id,
                occurred_at,
                source_kind,
                source_id,
                item_key,
            )
        context_columns = ""
        if normalized_category == "removed":
            context_columns = """
                , favorite_occurred_at, favorite_restored, favorite_source_id
                , watch_later_occurred_at, watch_later_restored, watch_later_source_id
                , dismiss_occurred_at, dismiss_source_id
                , dislike_occurred_at, dislike_source_id
            """

        connection = self.conn
        owns_read_snapshot = not connection.in_transaction
        if owns_read_snapshot:
            connection.execute("BEGIN")
        try:
            rows = connection.execute(
                cte
                + f"""
                    SELECT
                        item_key, source_platform, content_id, content_url,
                        content_type, title, author_name, cover_url, body_text,
                        recommendation_id, occurred_at, context, restored,
                        source_kind, source_id
                        {context_columns},
                        (SELECT COUNT(*) FROM entries) AS history_total,
                        (SELECT event_anchor FROM history_snapshot) AS history_event_anchor,
                        (SELECT recommendation_anchor FROM history_snapshot)
                            AS history_recommendation_anchor,
                        (SELECT removal_anchor FROM history_snapshot) AS history_removal_anchor
                    FROM entries
                    {cursor_clause}
                    ORDER BY occurred_at DESC, source_kind DESC, source_id DESC, item_key ASC
                    LIMIT ? OFFSET ?
                """,
                (*params, *cursor_params, page_limit + 1, page_offset),
            ).fetchall()
            if rows:
                total = int(rows[0]["history_total"] or 0)
            elif page_offset > 0 or cursor is not None:
                # An empty OFFSET/keyset page has no row to carry the scalar
                # total.  This fallback remains in the same explicit read
                # transaction, so it cannot observe a different WAL head.
                count_row = connection.execute(
                    cte + " SELECT COUNT(*) AS count FROM entries",
                    params,
                ).fetchone()
                total = int(count_row["count"] or 0) if count_row is not None else 0
            else:
                total = 0
        except Exception:
            if owns_read_snapshot:
                connection.rollback()
            raise
        else:
            if owns_read_snapshot:
                connection.commit()

        has_more = len(rows) > page_limit
        page_rows = rows[:page_limit]
        next_position: tuple[str, int, int, str, int, int, int] | None = None
        if has_more and page_rows:
            last_row = page_rows[-1]
            next_position = (
                str(last_row["occurred_at"] or ""),
                int(last_row["source_kind"] or 0),
                int(last_row["source_id"] or 0),
                str(last_row["item_key"] or ""),
                int(last_row["history_event_anchor"] or 0),
                int(last_row["history_recommendation_anchor"] or 0),
                int(last_row["history_removal_anchor"] or 0),
            )

        result: list[dict[str, Any]] = []
        for row in page_rows:
            item = dict(row)
            item.pop("history_total", None)
            item.pop("history_event_anchor", None)
            item.pop("history_recommendation_anchor", None)
            item.pop("history_removal_anchor", None)
            item.pop("source_kind", None)
            item.pop("source_id", None)
            contexts: list[dict[str, Any]] = []
            if normalized_category == "removed":
                context_specs = (
                    ("favorite", "favorite", 2),
                    ("watch_later", "watch_later", 2),
                    ("dismiss", "dismiss", 1),
                    ("dislike", "dislike", 1),
                )
                sortable_contexts: list[tuple[str, int, int, dict[str, Any]]] = []
                for context_name, column_prefix, context_source_kind in context_specs:
                    context_occurred_at = str(item.pop(f"{column_prefix}_occurred_at", "") or "")
                    context_source_id = int(item.pop(f"{column_prefix}_source_id", 0) or 0)
                    if not context_occurred_at:
                        if context_name in {"favorite", "watch_later"}:
                            item.pop(f"{column_prefix}_restored", None)
                        continue
                    context_restored = False
                    if context_name in {"favorite", "watch_later"}:
                        context_restored = bool(item.pop(f"{column_prefix}_restored", 0))
                    detail = {
                        "context": context_name,
                        "occurred_at": context_occurred_at,
                        "restored": context_restored,
                    }
                    sortable_contexts.append(
                        (
                            context_occurred_at,
                            context_source_kind,
                            context_source_id,
                            detail,
                        )
                    )
                # Establish the final ASC tie-break before the stable DESC
                # sort for the first three order dimensions.
                sortable_contexts.sort(key=lambda value: str(value[3]["context"]))
                sortable_contexts.sort(
                    key=lambda value: (value[0], value[1], value[2]),
                    reverse=True,
                )
                contexts = [value[3] for value in sortable_contexts]
            item["contexts"] = contexts
            result.append(item)
        return result, total, has_more, next_position

    def close(self) -> None:
        """Close owned workers, then every thread-affine SQLite connection."""
        for attribute in ("_maintenance_executor", "_serve_executor"):
            executor = getattr(self, attribute)
            if executor is None:
                continue
            executor.shutdown(wait=True, cancel_futures=True)
            setattr(self, attribute, None)
        with self._connection_registry_lock:
            primary = self._conn
            connections = list(self._connections_by_thread.values())
            if primary is not None:
                connections.append(primary)
            self._connections_by_thread = {}
            self._connection_owner_thread_id = None
            self._conn = None
        unique_connections = {id(connection): connection for connection in connections}
        for connection in unique_connections.values():
            connection.close()

    def _ensure_llm_usage_cache_columns(self) -> None:
        """Backfill v0.3.28+ prompt-cache columns on existing llm_usage tables."""
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(llm_usage)").fetchall()
        }
        required_columns = {
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE llm_usage ADD COLUMN {column_name} {column_type}")

    def _ensure_event_source_columns(self) -> None:
        """Add durable event attribution without guessing over legacy rows.

        New producers write canonical columns directly.  Existing databases
        are backfilled only from explicit metadata or a canonical URL; rows
        that cannot be identified remain blank/``legacy_unknown`` so a future
        platform-revocation flow can fail closed instead of deleting the wrong
        source.
        """
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        added_columns = False
        required_columns = {
            "source_platform": "TEXT NOT NULL DEFAULT ''",
            "content_id": "TEXT NOT NULL DEFAULT ''",
            "source_confidence": "TEXT NOT NULL DEFAULT 'legacy_unknown'",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                self.conn.execute(f"ALTER TABLE events ADD COLUMN {column_name} {column_type}")
                added_columns = True
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_source_content "
            "ON events(source_platform, content_id)"
        )

        # Existing rows need this compatibility backfill only when the
        # additive columns are first introduced. Valid rows with an empty
        # content_id are legitimate (for example a follow event), so using
        # that emptiness as a startup trigger would rescan the whole table on
        # every process start.
        if not added_columns:
            return

        rows = self.conn.execute(
            """
            SELECT id, url, metadata, source_platform, content_id, source_confidence
            FROM events
            WHERE COALESCE(source_platform, '') = ''
               OR COALESCE(content_id, '') = ''
               OR COALESCE(source_confidence, '') NOT IN (?, ?, ?)
            """,
            (
                SOURCE_CONFIDENCE_EXACT,
                SOURCE_CONFIDENCE_INFERRED,
                SOURCE_CONFIDENCE_LEGACY_UNKNOWN,
            ),
        ).fetchall()
        updates: list[tuple[str, str, str, int]] = []
        for row in rows:
            metadata: dict[str, Any] = {}
            raw_metadata = row["metadata"]
            if isinstance(raw_metadata, str) and raw_metadata:
                try:
                    decoded = json.loads(raw_metadata)
                except (TypeError, ValueError):
                    decoded = {}
                if isinstance(decoded, dict):
                    metadata = decoded

            current_confidence = str(row["source_confidence"] or "").strip()
            current_platform = normalize_source_platform(row["source_platform"])
            metadata_platform = str(metadata.get("source_platform") or "").strip()
            legacy_platform = (
                current_platform
                if current_confidence == SOURCE_CONFIDENCE_LEGACY_UNKNOWN
                else ""
            )
            # The old API filled missing source_platform with B站. Treat that
            # particular legacy value as a fallback, while still accepting an
            # explicit non-B站 metadata tag and URL inference. If there is no
            # stronger signal, keep B站 as a legacy-unknown attribution rather
            # than dropping the only source hint entirely.
            if (
                current_confidence == SOURCE_CONFIDENCE_LEGACY_UNKNOWN
                and normalize_source_platform(metadata_platform) == _BILIBILI_SOURCE_FAMILY
            ):
                legacy_platform = legacy_platform or _BILIBILI_SOURCE_FAMILY
                metadata_platform = ""
            authoritative_platform = (
                current_platform
                if current_confidence in {SOURCE_CONFIDENCE_EXACT, SOURCE_CONFIDENCE_INFERRED}
                else ""
            )
            platform, detected_confidence = resolve_source_attribution(
                explicit_platform=authoritative_platform,
                metadata_platform=metadata_platform,
                url=row["url"],
                legacy_platform=legacy_platform,
            )
            if not platform:
                platform = current_platform
            confidence = (
                current_confidence
                if current_confidence
                in {SOURCE_CONFIDENCE_EXACT, SOURCE_CONFIDENCE_INFERRED}
                and current_platform
                else detected_confidence
            )
            content_id = str(row["content_id"] or "").strip() or extract_source_content_id(metadata)
            if (
                platform != current_platform
                or content_id != str(row["content_id"] or "").strip()
                or confidence != current_confidence
            ):
                updates.append((platform, content_id, confidence, int(row["id"])))

        if updates:
            self.conn.executemany(
                """
                UPDATE events
                SET source_platform = ?, content_id = ?, source_confidence = ?
                WHERE id = ?
                """,
                updates,
            )

    def _ensure_event_satisfaction_columns(self) -> None:
        """Backfill v0.3.x event-satisfaction columns for pre-migration DBs.

        Existing rows keep ``NULL`` in both columns; consumers treat NULL
        as ``unknown`` so the upgrade is non-blocking.
        """
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        required_columns = {
            "inferred_satisfaction": "TEXT",
            "satisfaction_reason": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE events ADD COLUMN {column_name} {column_type}")

    def _ensure_event_ingest_key_column(self) -> None:
        """Add the producer idempotency key and repeatable partial unique index."""
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        if "ingest_key" not in existing_columns:
            self.conn.execute("ALTER TABLE events ADD COLUMN ingest_key TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_ingest_key_unique
            ON events(ingest_key) WHERE ingest_key <> ''
            """
        )

    def _ensure_recommendation_feedback_columns(self) -> None:
        """Backfill recommendation feedback columns for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        required_columns = {
            "feedback_type": "TEXT",
            "feedback_note": "TEXT",
            "feedback_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE recommendations ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_runtime_columns(self) -> None:
        """Backfill content-cache runtime columns for continuous refresh."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "last_scored_at": "TIMESTAMP",
            "notification_sent": "INTEGER DEFAULT 0",
            "notified_at": "TIMESTAMP",
            "pool_status": "TEXT DEFAULT 'fresh'",
            "recommended_at": "TIMESTAMP",
            "feedback_type": "TEXT",
            "feedback_at": "TIMESTAMP",
            "source": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_relevance_columns(self) -> None:
        """Backfill relevance fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "relevance_score": "REAL DEFAULT 0.0",
            "relevance_reason": "TEXT DEFAULT ''",
            "candidate_tier": "TEXT DEFAULT 'primary'",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_temporal_columns(self) -> None:
        """Backfill evaluator-owned temporal metadata for existing caches."""

        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "temporal_class": "TEXT DEFAULT 'unknown'",
            "temporal_confidence": "REAL DEFAULT 0.0",
            "temporal_reason": "TEXT DEFAULT ''",
            "temporal_policy_version": "TEXT DEFAULT 'v1'",
            "temporal_validity_mode": "TEXT DEFAULT 'none'",
            "temporal_valid_until": "TEXT DEFAULT ''",
            "temporal_scope": "TEXT DEFAULT 'none'",
            "temporal_state": "TEXT DEFAULT 'unknown'",
            "temporal_evidence": "TEXT DEFAULT ''",
            "temporal_next_review_at": "TEXT DEFAULT ''",
            "temporal_evaluated_at": "TEXT DEFAULT ''",
            "temporal_evidence_complete": "INTEGER DEFAULT 0",
            "temporal_review_attempts": "INTEGER DEFAULT 0",
            "temporal_review_retry_at": "TEXT DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_cache_temporal_review "
            "ON content_cache(pool_status, temporal_next_review_at, bvid)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_cache_temporal_review_retry "
            "ON content_cache(pool_status, temporal_review_retry_at, bvid)"
        )

    def _ensure_content_cache_topic_columns(self) -> None:
        """Backfill topic bucketing fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        if "topic_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_key TEXT DEFAULT ''")
        if "topic_group" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_group TEXT DEFAULT ''")
        if "style_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN style_key TEXT DEFAULT ''")
        if "franchise_key" not in existing_columns:
            # v0.3.18: LLM-tagged IP / franchise / series. Empty string for
            # general-interest content; non-empty rows let the curator
            # propagate dislikes within an IP and let
            # /api/recommendations cap how many same-franchise items
            # appear in a single response window — without relying on
            # any title-string heuristic or hardcoded alias list.
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN franchise_key TEXT DEFAULT ''")

    def _ensure_content_cache_pool_copy_columns(self) -> None:
        """Backfill precomputed pool-copy fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "pool_expression": "TEXT DEFAULT ''",
            "pool_topic_label": "TEXT DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_delight_columns(self) -> None:
        """Backfill proactive delight scoring fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "delight_score": "REAL DEFAULT 0.0",
            "delight_reason": "TEXT DEFAULT ''",
            "delight_hook": "TEXT DEFAULT ''",
            "delight_notified": "INTEGER DEFAULT 0",
            "delight_notified_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_keyframe_columns(self) -> None:
        """Add video-keyframe prewarm bookkeeping for existing databases.

        Frame vectors live in the embedding cache under provenance-aware
        ``keyframe_embedding_cache_key`` keys. A completion timestamp is only
        written for confirmed no-data or a complete sampled run whose every
        frame embedding succeeded; transient fetch, sprite, parser, and embed
        failures remain eligible for the next prewarm cycle.
        """
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "keyframes_fetched_at": "TIMESTAMP",
            "keyframe_count": "INTEGER DEFAULT 0",
            "keyframe_embedding_fingerprint": "TEXT DEFAULT ''",
            "keyframe_embedding_dimension": "INTEGER DEFAULT 0",
            "keyframe_sampling_signature": "TEXT DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def get_candidates_needing_keyframes(
        self,
        *,
        limit: int = 50,
        embedding_fingerprint: str = "",
        embedding_dimension: int = 0,
        sampling_signature: str = "",
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Return current-pool Bilibili rows needing fetch or re-embedding.

        Videoshot data only exists for Bilibili videos, so non-Bilibili and
        text-shaped rows are excluded rather than retried forever.
        """
        self._ensure_fresh_read()
        clause, clause_params = self._pool_servable_where_clause_on(
            self.conn,
            xhs_self_nickname,
            pool_status="fresh",
        )
        provenance_parts = ["keyframes_fetched_at IS NULL"]
        params: list[Any] = [*clause_params]
        fingerprint = str(embedding_fingerprint or "").strip()
        signature = str(sampling_signature or "").strip()
        # keyframe_count=0 is a confirmed source no-data result. A model /
        # endpoint or sampling change must re-embed existing vectors, but it
        # must not turn a confirmed empty source into an endless refetch loop.
        enriched = "COALESCE(keyframe_count, 0) > 0"
        if fingerprint:
            provenance_parts.append(
                f"({enriched} AND COALESCE(keyframe_embedding_fingerprint, '') != ?)"
            )
            params.append(fingerprint)
        requested_dimension = max(0, int(embedding_dimension))
        if requested_dimension > 0:
            # Stored zero is also unknown, not a proven incompatible vector
            # space. Only two observed, positive dimensions can mismatch.
            provenance_parts.append(
                f"({enriched} AND COALESCE(keyframe_embedding_dimension, 0) > 0 "
                "AND COALESCE(keyframe_embedding_dimension, 0) != ?)"
            )
            params.append(requested_dimension)
        if signature:
            provenance_parts.append(
                f"({enriched} AND COALESCE(keyframe_sampling_signature, '') != ?)"
            )
            params.append(signature)
        provenance_sql = "(" + " OR ".join(provenance_parts) + ")"
        cursor = self.conn.execute(
            f"""
            SELECT bvid, title, cover_url, keyframe_count,
                   keyframe_embedding_fingerprint, keyframe_embedding_dimension,
                   keyframe_sampling_signature,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE {clause}
              AND {provenance_sql}
              AND COALESCE(bvid, '') != ''
              AND COALESCE(NULLIF(source_platform, ''), 'bilibili') = 'bilibili'
              AND COALESCE(NULLIF(content_type, ''), 'video') = 'video'
            ORDER BY COALESCE(relevance_score, 0) DESC
            """,
            params,
        )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        return rows[: max(1, int(limit))]

    def mark_keyframes_fetched(
        self,
        bvid: str,
        *,
        keyframe_count: int = 0,
        embedding_fingerprint: str = "",
        embedding_dimension: int = 0,
        sampling_signature: str = "",
    ) -> None:
        """Stamp a confirmed no-data or completely embedded keyframe run."""
        key = (bvid or "").strip()
        if not key:
            return
        self.conn.execute(
            """
            UPDATE content_cache
            SET keyframes_fetched_at = CURRENT_TIMESTAMP,
                keyframe_count = ?,
                keyframe_embedding_fingerprint = ?,
                keyframe_embedding_dimension = ?,
                keyframe_sampling_signature = ?
            WHERE bvid = ?
            """,
            (
                max(0, int(keyframe_count)),
                str(embedding_fingerprint or "").strip(),
                max(0, int(embedding_dimension)),
                str(sampling_signature or "").strip(),
                key,
            ),
        )
        self.conn.commit()

    def _ensure_content_cache_danmaku_columns(self) -> None:
        """Add danmaku-text enrichment fields for existing databases.

        Deliberately NOT reusing ``body_text``: that column renders into the
        card body on all three surfaces (extension / desktop / mobile web) and
        feeds five LLM prompts, so danmaku there would turn card bodies into
        walls of "已取餐". The source timestamp is written for a confirmed
        empty response, while HTTP, parse, and embedding failures remain
        retryable; existing summaries can be re-embedded without refetching.
        """
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "danmaku_text": "TEXT DEFAULT ''",
            "danmaku_fetched_at": "TIMESTAMP",
            "danmaku_embedding_fingerprint": "TEXT DEFAULT ''",
            "danmaku_embedding_dimension": "INTEGER DEFAULT 0",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def get_candidates_needing_danmaku(
        self,
        *,
        limit: int = 50,
        embedding_fingerprint: str = "",
        embedding_dimension: int = 0,
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Return current-pool Bilibili rows needing fetch or re-embedding.

        A model/provider change selects rows with an existing summary without
        requiring another source fetch; the caller can embed ``danmaku_text``
        directly. Rows with a blank summary still only enter the normal fetch
        path when their source fetch has never completed.
        """
        self._ensure_fresh_read()
        clause, clause_params = self._pool_servable_where_clause_on(
            self.conn,
            xhs_self_nickname,
            pool_status="fresh",
        )
        provenance_parts = ["danmaku_fetched_at IS NULL"]
        params: list[Any] = [*clause_params]
        fingerprint = str(embedding_fingerprint or "").strip()
        if fingerprint:
            # A confirmed empty source has no vector to re-embed. Provider or
            # dimension changes only requeue rows with a stored summary.
            enriched = "COALESCE(danmaku_text, '') != ''"
            provenance_parts.append(
                f"({enriched} AND COALESCE(danmaku_embedding_fingerprint, '') != ?)"
            )
            params.append(fingerprint)
        else:
            enriched = "COALESCE(danmaku_text, '') != ''"
        requested_dimension = max(0, int(embedding_dimension))
        if requested_dimension > 0:
            # Stored zero is unknown, not a proven incompatible vector space.
            provenance_parts.append(
                f"({enriched} AND COALESCE(danmaku_embedding_dimension, 0) > 0 "
                "AND COALESCE(danmaku_embedding_dimension, 0) != ?)"
            )
            params.append(requested_dimension)
        provenance_sql = "(" + " OR ".join(provenance_parts) + ")"
        cursor = self.conn.execute(
            f"""
            SELECT bvid, title, danmaku_text, danmaku_fetched_at,
                   danmaku_embedding_fingerprint, danmaku_embedding_dimension,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE {clause}
              AND {provenance_sql}
              AND COALESCE(bvid, '') != ''
              AND COALESCE(NULLIF(source_platform, ''), 'bilibili') = 'bilibili'
              AND COALESCE(NULLIF(content_type, ''), 'video') = 'video'
            ORDER BY COALESCE(relevance_score, 0) DESC
            """,
            params,
        )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        return rows[: max(1, int(limit))]

    def update_danmaku_text(
        self,
        bvid: str,
        *,
        danmaku_text: str = "",
        embedding_fingerprint: str = "",
        embedding_dimension: int = 0,
    ) -> None:
        """Store a successful summary and stamp source + embedding provenance."""
        key = (bvid or "").strip()
        if not key:
            return
        self.conn.execute(
            """
            UPDATE content_cache
            SET danmaku_text = ?,
                danmaku_fetched_at = CURRENT_TIMESTAMP,
                danmaku_embedding_fingerprint = ?,
                danmaku_embedding_dimension = ?
            WHERE bvid = ?
            """,
            (
                danmaku_text or "",
                str(embedding_fingerprint or "").strip(),
                max(0, int(embedding_dimension)),
                key,
            ),
        )
        self.conn.commit()

    def update_danmaku_embedding_provenance(
        self,
        bvid: str,
        *,
        embedding_fingerprint: str,
        embedding_dimension: int,
    ) -> None:
        """Mark an existing successful summary as embedded by this namespace."""
        key = (bvid or "").strip()
        if not key:
            return
        self.conn.execute(
            """
            UPDATE content_cache
            SET danmaku_embedding_fingerprint = ?,
                danmaku_embedding_dimension = ?
            WHERE bvid = ? AND COALESCE(danmaku_text, '') != ''
            """,
            (
                str(embedding_fingerprint or "").strip(),
                max(0, int(embedding_dimension)),
                key,
            ),
        )
        self.conn.commit()

    def get_danmaku_texts_for(self, bvids: list[str]) -> dict[str, str]:
        """Map bvid → stored danmaku text for the given ids (empty ones omitted)."""
        keys = [b.strip() for b in bvids if (b or "").strip()]
        if not keys:
            return {}
        out: dict[str, str] = {}
        # Chunk to stay clear of SQLite's variable limit on large pool windows.
        for start in range(0, len(keys), 400):
            chunk = keys[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT bvid, COALESCE(danmaku_text, '') AS danmaku_text
                FROM content_cache
                WHERE bvid IN ({placeholders})
                """,
                tuple(chunk),
            )
            for row in cursor.fetchall():
                text = str(row["danmaku_text"] or "").strip()
                if text:
                    out[str(row["bvid"])] = text
        return out

    def _ensure_content_cache_multisource_columns(self) -> None:
        """Add multi-source content identity fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "content_id": "TEXT DEFAULT ''",
            "content_url": "TEXT DEFAULT ''",
            "source_platform": "TEXT DEFAULT 'bilibili'",
            "author_name": "TEXT DEFAULT ''",
            "body_text": "TEXT DEFAULT ''",
            "content_type": "TEXT DEFAULT 'video'",
            "favorite_count": "INTEGER DEFAULT 0",
            "collect_count": "INTEGER DEFAULT 0",
            "comment_count": "INTEGER DEFAULT 0",
            "share_count": "INTEGER DEFAULT 0",
            "danmaku_count": "INTEGER DEFAULT 0",
            "reply_count": "INTEGER DEFAULT 0",
            "retweet_count": "INTEGER DEFAULT 0",
            "bookmark_count": "INTEGER DEFAULT 0",
            "published_at": "TEXT NOT NULL DEFAULT ''",
            "published_label": "TEXT NOT NULL DEFAULT ''",
            # P1.8 yield provenance: the discovery_keywords.id that produced this
            # row (NULL for legacy / non-search / flag-off). Nullable, additive.
            "source_keyword_id": "INTEGER",
            "rating_score": "REAL DEFAULT 0.0",
            "rating_count": "INTEGER DEFAULT 0",
            "source_rank": "INTEGER DEFAULT 0",
        }
        added = False
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")
            added = True
        if added:
            self.conn.execute("UPDATE content_cache SET content_id = bvid WHERE content_id = ''")

    def _ensure_content_identity_columns(self) -> None:
        """Backfill canonical identities without breaking legacy cache writers.

        Desktop v0.3.166 and older omit ``content_cache.item_key``. A full
        unique index therefore turns every legacy insert after the first into
        ``UNIQUE constraint failed`` because the additive column defaults to an
        empty string. Keep uniqueness for canonical, non-empty identities while
        allowing legacy rows to remain blank until a current runtime starts and
        repairs them here.
        """
        cache_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        if "item_key" not in cache_columns:
            self.conn.execute(
                "ALTER TABLE content_cache ADD COLUMN item_key TEXT NOT NULL DEFAULT ''"
            )

        cache_rows = self.conn.execute(
            """
            SELECT bvid, item_key, source_platform, content_id, content_url
            FROM content_cache
            WHERE item_key = ''
            """
        ).fetchall()
        identity_index = self.conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_content_cache_item_key'
            """
        ).fetchone()
        index_sql = str(identity_index["sql"] or "") if identity_index is not None else ""
        normalized_index_sql = " ".join(index_sql.lower().split())
        expected_partial_predicate = "where item_key != ''"
        index_needs_rebuild = identity_index is not None and not normalized_index_sql.endswith(
            expected_partial_predicate
        )
        # Drop the uniqueness guard while blank identities are expanded. A
        # blank legacy row can normalize to an identity already held by a
        # current row; consolidation below must see both rows before the guard
        # is restored.
        if cache_rows or index_needs_rebuild:
            self.conn.execute("DROP INDEX IF EXISTS idx_content_cache_item_key")
        for row in cache_rows:
            storage_key = str(row["bvid"] or "").strip()
            source_platform = str(row["source_platform"] or "").strip()
            platform = canonical_source_platform(source_platform or "bilibili")
            content_id = str(row["content_id"] or "").strip()
            if not source_platform:
                content_id = storage_key
            item_key = make_item_key(
                platform,
                content_id or storage_key,
                str(row["content_url"] or ""),
            )
            self.conn.execute(
                "UPDATE content_cache SET item_key = ? WHERE bvid = ?",
                (item_key, storage_key),
            )

        recommendation_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        if "item_key" not in recommendation_columns:
            self.conn.execute(
                "ALTER TABLE recommendations ADD COLUMN item_key TEXT NOT NULL DEFAULT ''"
            )
        self._consolidate_content_identity_duplicates()
        self.conn.execute(
            """
            UPDATE recommendations AS r
            SET item_key = COALESCE(
                (SELECT c.item_key FROM content_cache AS c WHERE c.bvid = r.bvid),
                ''
            )
            WHERE r.item_key = ''
            """
        )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_content_cache_item_key
            ON content_cache (item_key)
            WHERE item_key != ''
            """
        )
        if cache_rows:
            logger.info(
                "Repaired %d legacy content-cache row(s) with blank canonical identity",
                len(cache_rows),
            )
        if index_needs_rebuild:
            logger.info(
                "Made content-cache identity index compatible with legacy blank-key writers"
            )

    def _ensure_seen_items_ledger(self) -> None:
        """Create and incrementally backfill the all-time canonical seen ledger."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                item_key        TEXT PRIMARY KEY,
                source_platform TEXT NOT NULL,
                content_id      TEXT NOT NULL,
                first_event_id  INTEGER NOT NULL,
                last_event_id   INTEGER NOT NULL,
                first_seen_at   TIMESTAMP NOT NULL,
                last_seen_at    TIMESTAMP NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_seen_items_platform_content
                ON seen_items(source_platform, content_id);
            CREATE TABLE IF NOT EXISTS seen_items_backfill_state (
                singleton             INTEGER PRIMARY KEY CHECK (singleton = 1),
                last_scanned_event_id INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO seen_items_backfill_state (
                singleton, last_scanned_event_id
            ) VALUES (1, 0);
            """
        )
        # The scan cursor is type-set aware. When _SEEN_ITEM_EVENT_TYPES grows
        # (2026-07-26: favourites / likes / coins joined views), every existing
        # install still has its cursor parked at the newest event, so the newly
        # eligible history would never be scanned. Bumping the recorded version
        # rewinds the cursor exactly once per widening.
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(seen_items_backfill_state)").fetchall()
        }
        if "scanned_event_types_version" not in existing_columns:
            self.conn.execute(
                "ALTER TABLE seen_items_backfill_state "
                "ADD COLUMN scanned_event_types_version INTEGER NOT NULL DEFAULT 0"
            )
        state = self.conn.execute(
            """
            SELECT last_scanned_event_id, scanned_event_types_version
            FROM seen_items_backfill_state
            WHERE singleton = 1
            """
        ).fetchone()
        last_scanned_event_id = int(state["last_scanned_event_id"] if state else 0)
        scanned_version = int(state["scanned_event_types_version"] if state else 0)
        if scanned_version < _SEEN_ITEM_EVENT_TYPES_VERSION:
            last_scanned_event_id = 0
            self.conn.execute(
                """
                UPDATE seen_items_backfill_state
                SET last_scanned_event_id = 0, scanned_event_types_version = ?
                WHERE singleton = 1
                """,
                (_SEEN_ITEM_EVENT_TYPES_VERSION,),
            )
        placeholders = ", ".join("?" for _ in _SEEN_ITEM_EVENT_TYPES_ORDERED)
        rows = self.conn.execute(
            f"""
            SELECT id, url, metadata, created_at
            FROM events
            WHERE event_type IN ({placeholders}) AND id > ?
            ORDER BY id ASC
            """,
            (*_SEEN_ITEM_EVENT_TYPES_ORDERED, last_scanned_event_id),
        ).fetchall()
        backfilled = 0
        for row in rows:
            if self._upsert_seen_items_from_view_event_on(
                self.conn,
                event_id=int(row["id"]),
                row=dict(row),
                seen_at=str(row["created_at"] or ""),
            ):
                backfilled += 1
        latest = self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS latest_id FROM events"
        ).fetchone()
        self._advance_seen_items_cursor_on(
            self.conn,
            int(latest["latest_id"] if latest else 0),
        )
        if rows:
            self._invalidate_seen_state_cache()
        if backfilled:
            logger.info(
                "Backfilled %d legacy consumed-content event(s) into the seen-item ledger",
                backfilled,
            )

    @staticmethod
    def _content_identity_metadata_missing(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() in {"", "[]", "{}"}
        if isinstance(value, (int, float)):
            return value == 0
        return False

    def _consolidate_content_identity_duplicates(self) -> None:
        """Merge legacy cache rows that normalize to one canonical identity."""
        duplicate_keys = self.conn.execute(
            """
            SELECT item_key
            FROM content_cache
            WHERE item_key != ''
            GROUP BY item_key
            HAVING COUNT(*) > 1
            ORDER BY item_key
            """
        ).fetchall()
        if not duplicate_keys:
            return

        columns = [
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        ]
        merge_columns = [column for column in columns if column not in {"bvid", "item_key"}]
        temporal_columns = {
            "published_at",
            "published_label",
            "temporal_class",
            "temporal_confidence",
            "temporal_reason",
            "temporal_policy_version",
            *_TEMPORAL_LIFECYCLE_FIELDS,
        }
        temporal_risk_order = {
            "breaking": 0,
            "current": 1,
            "versioned": 2,
            "evergreen": 3,
            "historical": 4,
        }
        temporal_now = datetime.now(UTC)
        for duplicate in duplicate_keys:
            item_key = str(duplicate["item_key"])
            members = [
                dict(row)
                for row in self.conn.execute(
                    "SELECT * FROM content_cache WHERE item_key = ? ORDER BY bvid",
                    (item_key,),
                ).fetchall()
            ]
            canonical_members: list[dict[str, Any]] = []
            for member in members:
                if str(member["bvid"]) == item_key:
                    canonical_members.append(member)
                    continue
                platform = canonical_source_platform(
                    str(member.get("source_platform") or "bilibili")
                )
                content_id = str(member.get("content_id") or member.get("bvid") or "")
                try:
                    expected_storage_key = content_storage_key(
                        platform,
                        content_id,
                        str(member.get("content_url") or ""),
                    )
                except ValueError:
                    continue
                if str(member["bvid"]) == expected_storage_key:
                    canonical_members.append(member)
            keeper = min(canonical_members or members, key=lambda row: str(row["bvid"]))
            keeper_bvid = str(keeper["bvid"])
            merged = dict(keeper)
            for member in members:
                for column in merge_columns:
                    if column in temporal_columns:
                        continue
                    if self._content_identity_metadata_missing(
                        merged.get(column)
                    ) and not self._content_identity_metadata_missing(member.get(column)):
                        merged[column] = member[column]

            # Publication and evaluator-owned temporal metadata form one
            # evidence group.  Prefer any evidence that already proves the
            # identity stale; otherwise select deterministically by temporal
            # risk, confidence, publication time, then storage key.  Picking
            # fields independently can synthesize ``breaking/.95 + now`` from
            # two legacy rows and resurrect an expired duplicate.
            temporal_sources: list[
                tuple[
                    tuple[int, int, float, datetime, str],
                    dict[str, Any],
                    tuple[Any, ...],
                ]
            ] = []
            for member in members:
                normalized, normalized_lifecycle = _normalize_temporal_evidence_group_for_storage(
                    member
                )
                if normalized[0] == "unknown":
                    continue
                temporal_source = {
                    **member,
                    "temporal_class": normalized[0],
                    "temporal_confidence": normalized[1],
                    "temporal_reason": normalized[2],
                    "temporal_policy_version": normalized[3],
                    **dict(
                        zip(
                            _TEMPORAL_LIFECYCLE_FIELDS,
                            normalized_lifecycle,
                            strict=True,
                        )
                    ),
                }
                decision = self._temporal_decision_for_row(
                    temporal_source,
                    now=temporal_now,
                )
                disposition = _temporal_decision_disposition(decision)
                publication_clock = _publication_datetime_for_merge(
                    member.get("published_at")
                ) or datetime.max.replace(tzinfo=UTC)
                temporal_sources.append(
                    (
                        (
                            {"expired": 0, "review_due": 1}.get(disposition, 2),
                            temporal_risk_order.get(normalized[0], 99),
                            -normalized[1],
                            publication_clock,
                            str(member.get("bvid") or ""),
                        ),
                        temporal_source,
                        (*normalized, *normalized_lifecycle),
                    )
                )

            merged_published_at = ""
            merged_published_label = ""
            if temporal_sources:
                _, temporal_source, normalized_temporal = min(
                    temporal_sources,
                    key=lambda source: source[0],
                )
                merged_published_at = str(temporal_source.get("published_at") or "")
                merged_published_label = str(temporal_source.get("published_label") or "")
                merged.update(
                    {
                        "temporal_class": normalized_temporal[0],
                        "temporal_confidence": normalized_temporal[1],
                        "temporal_reason": normalized_temporal[2],
                        "temporal_policy_version": normalized_temporal[3],
                        **dict(
                            zip(
                                _TEMPORAL_LIFECYCLE_FIELDS,
                                normalized_temporal[4:],
                                strict=True,
                            )
                        ),
                    }
                )
            else:
                merged.update(
                    {
                        "temporal_class": "unknown",
                        "temporal_confidence": 0.0,
                        "temporal_reason": "",
                        "temporal_policy_version": TEMPORAL_POLICY_VERSION,
                        "temporal_validity_mode": "none",
                        "temporal_valid_until": "",
                        "temporal_scope": "none",
                        "temporal_state": "unknown",
                        "temporal_evidence": "",
                        "temporal_next_review_at": "",
                        "temporal_evaluated_at": "",
                        "temporal_evidence_complete": 0,
                    }
                )
                merged_published_at = str(keeper.get("published_at") or "")
                merged_published_label = str(keeper.get("published_label") or "")
                for member in members:
                    merged_published_at, merged_published_label = (
                        _merge_publication_evidence_conservatively(
                            existing_at=merged_published_at,
                            existing_label=merged_published_label,
                            incoming_at=member.get("published_at"),
                            incoming_label=member.get("published_label"),
                        )
                    )
            merged["published_at"] = merged_published_at
            merged["published_label"] = merged_published_label

            if temporal_sources:
                merged_decision = self._temporal_decision_for_row(
                    merged,
                    now=temporal_now,
                )
                disposition = _temporal_decision_disposition(merged_decision)
                pool_status = str(merged.get("pool_status") or "fresh")
                if disposition == "expired" and pool_status in {
                    "fresh",
                    "suppressed",
                    "temporal_review_hold",
                }:
                    merged["pool_status"] = "stale"
                elif disposition == "review_due" and pool_status in {
                    "fresh",
                    "suppressed",
                    "temporal_review_hold",
                }:
                    merged["pool_status"] = "temporal_review_hold"
                elif disposition == "eligible" and pool_status == "temporal_review_hold":
                    merged["pool_status"] = "fresh"

            changed_columns = [
                column for column in merge_columns if merged.get(column) != keeper.get(column)
            ]
            if changed_columns:
                assignments = ", ".join(f"{column} = ?" for column in changed_columns)
                self.conn.execute(
                    f"UPDATE content_cache SET {assignments} WHERE bvid = ?",
                    [*(merged[column] for column in changed_columns), keeper_bvid],
                )

            member_bvids = [str(member["bvid"]) for member in members]
            self._redirect_legacy_saved_identity(member_bvids, item_key)
            placeholders = ", ".join("?" for _ in member_bvids)
            self.conn.execute(
                f"""
                UPDATE recommendations
                SET bvid = ?, item_key = ?
                WHERE bvid IN ({placeholders}) OR item_key = ?
                """,
                [keeper_bvid, item_key, *member_bvids, item_key],
            )
            removed_bvids = [bvid for bvid in member_bvids if bvid != keeper_bvid]
            if removed_bvids:
                removed_placeholders = ", ".join("?" for _ in removed_bvids)
                self.conn.execute(
                    f"DELETE FROM content_cache WHERE bvid IN ({removed_placeholders})",
                    removed_bvids,
                )

    def _redirect_legacy_saved_identity(self, storage_keys: list[str], item_key: str) -> None:
        """Preserve legacy saved-list identity before duplicate cache rows are removed."""
        if not storage_keys:
            return
        placeholders = ", ".join("?" for _ in storage_keys)
        for table_name in ("watch_later", "favorites"):
            exists = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if exists is None:
                continue
            self._ensure_legacy_saved_item_key_column(table_name)
            self.conn.execute(
                f"UPDATE {table_name} SET item_key = ? WHERE bvid IN ({placeholders})",
                [item_key, *storage_keys],
            )

    def _ensure_discovery_candidate_columns(self) -> None:
        """Backfill discovery-candidate lifecycle columns for existing databases."""

        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(discovery_candidates)").fetchall()
        }
        required_columns = {
            "score_threshold": "REAL NOT NULL DEFAULT 0.0",
            "eval_attempts": "INTEGER NOT NULL DEFAULT 0",
            "batch_eval_attempts": "INTEGER NOT NULL DEFAULT 0",
            "claim_token": "TEXT",
            "body_text": "TEXT NOT NULL DEFAULT ''",
            "favorite_count": "INTEGER NOT NULL DEFAULT 0",
            "collect_count": "INTEGER NOT NULL DEFAULT 0",
            "comment_count": "INTEGER NOT NULL DEFAULT 0",
            "share_count": "INTEGER NOT NULL DEFAULT 0",
            "danmaku_count": "INTEGER NOT NULL DEFAULT 0",
            "reply_count": "INTEGER NOT NULL DEFAULT 0",
            "retweet_count": "INTEGER NOT NULL DEFAULT 0",
            "bookmark_count": "INTEGER NOT NULL DEFAULT 0",
            "published_at": "TEXT NOT NULL DEFAULT ''",
            "published_label": "TEXT NOT NULL DEFAULT ''",
            # P1.8 yield provenance: nullable, additive (existing rows stay NULL).
            "source_keyword_id": "INTEGER",
            "rating_score": "REAL NOT NULL DEFAULT 0.0",
            "rating_count": "INTEGER NOT NULL DEFAULT 0",
            "source_rank": "INTEGER NOT NULL DEFAULT 0",
            "temporal_class": "TEXT NOT NULL DEFAULT 'unknown'",
            "temporal_confidence": "REAL NOT NULL DEFAULT 0.0",
            "temporal_reason": "TEXT NOT NULL DEFAULT ''",
            "temporal_policy_version": "TEXT NOT NULL DEFAULT 'v1'",
            "temporal_validity_mode": "TEXT NOT NULL DEFAULT 'none'",
            "temporal_valid_until": "TEXT NOT NULL DEFAULT ''",
            "temporal_scope": "TEXT NOT NULL DEFAULT 'none'",
            "temporal_state": "TEXT NOT NULL DEFAULT 'unknown'",
            "temporal_evidence": "TEXT NOT NULL DEFAULT ''",
            "temporal_next_review_at": "TEXT NOT NULL DEFAULT ''",
            "temporal_evaluated_at": "TEXT NOT NULL DEFAULT ''",
            "temporal_evidence_complete": "INTEGER NOT NULL DEFAULT 0",
            "temporal_review_attempts": "INTEGER NOT NULL DEFAULT 0",
            "temporal_review_retry_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(
                f"ALTER TABLE discovery_candidates ADD COLUMN {column_name} {column_type}"
            )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_temporal_review "
            "ON discovery_candidates(status, temporal_next_review_at, id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_temporal_retry "
            "ON discovery_candidates(status, temporal_review_retry_at, id)"
        )

    def _normalize_legacy_style_keys(self) -> None:
        """Rewrite known legacy content-form style keys to viewing-mode keys."""

        targets = (
            ("content_cache", "style_key"),
            ("discovery_candidates", "style_key"),
        )
        for table_name, column_name in targets:
            existing_columns = {
                str(row["name"])
                for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if column_name not in existing_columns:
                continue
            for legacy_key, style_key in _LEGACY_STYLE_KEY_MAP.items():
                self.conn.execute(
                    f"UPDATE {table_name} SET {column_name} = ? WHERE {column_name} = ?",
                    (style_key, legacy_key),
                )

    def _ensure_recommendation_read_indexes(self) -> None:
        """Create indexes used by recommendation and activity-feed reads.

        Pool readiness and maintenance repeatedly exclude rows already present
        in ``recommendations`` by BVID.  Without the BVID index, each
        ``NOT EXISTS`` probe scans the full recommendation history and can hold
        the API event loop for tens of seconds on a mature database.
        """
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_recommendations_created_id
                ON recommendations (created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_recommendations_bvid
                ON recommendations (bvid);
            CREATE INDEX IF NOT EXISTS idx_events_type_id
                ON events (event_type, id DESC);
            CREATE INDEX IF NOT EXISTS idx_content_cache_content_id
                ON content_cache (content_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_content_cache_item_key
                ON content_cache (item_key)
                WHERE item_key != '';
            CREATE INDEX IF NOT EXISTS idx_content_cache_item_key_lookup
                ON content_cache (item_key);
            CREATE INDEX IF NOT EXISTS idx_recommendations_item_key
                ON recommendations (item_key);
        """)

    def _ensure_content_cache_quality_columns(self) -> None:
        """Add QualityGate up_level/follower_count columns for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        if "up_level" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN up_level INTEGER DEFAULT 0")
        if "follower_count" not in existing_columns:
            sql = "ALTER TABLE content_cache ADD COLUMN follower_count INTEGER DEFAULT 0"
            self.conn.execute(sql)

    def _ensure_source_recipes_table(self) -> None:
        """Create the source_recipes table if it does not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_recipes (
                id            TEXT PRIMARY KEY,
                source_type   TEXT NOT NULL,
                name          TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                config        TEXT DEFAULT '{}',
                target_share  INTEGER DEFAULT 4,
                enabled       INTEGER DEFAULT 1,
                created_by    TEXT DEFAULT 'system',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_fetched_at TIMESTAMP
            );
        """)

    def _ensure_xhs_observed_urls_table(self) -> None:
        """Create the xhs_observed_urls table if it does not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS xhs_observed_urls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL,
                page_type   TEXT NOT NULL DEFAULT 'other',
                observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                enriched    INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_xhs_observed_urls_url
                ON xhs_observed_urls (url);
        """)

    def _ensure_chat_turns_table(self) -> None:
        """Create durable popup chat-turn storage for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_turns (
                turn_id       TEXT PRIMARY KEY,
                session       TEXT NOT NULL DEFAULT 'popup',
                scope         TEXT NOT NULL DEFAULT 'chat',
                subject_id    TEXT NOT NULL DEFAULT '',
                subject_title TEXT NOT NULL DEFAULT '',
                reply_to_turn_id TEXT NOT NULL DEFAULT '',
                message       TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'pending',
                reply         TEXT NOT NULL DEFAULT '',
                error         TEXT NOT NULL DEFAULT '',
                payload       TEXT NOT NULL DEFAULT '{}',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
                ON chat_turns(session, created_at, turn_id);
            CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
                ON chat_turns(scope, subject_id, created_at);
            CREATE TABLE IF NOT EXISTS card_settlements (
                ref        TEXT PRIMARY KEY,
                verdict    TEXT NOT NULL,
                turn_id    TEXT NOT NULL,
                payload    TEXT NOT NULL DEFAULT '{}',
                applied    INTEGER NOT NULL DEFAULT 0,
                result     TEXT NOT NULL DEFAULT '{}',
                event_id   TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(chat_turns)").fetchall()
        }
        if "payload" not in columns:
            self.conn.execute(
                "ALTER TABLE chat_turns ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'"
            )
        if "reply_to_turn_id" not in columns:
            self.conn.execute(
                "ALTER TABLE chat_turns ADD COLUMN reply_to_turn_id TEXT NOT NULL DEFAULT ''"
            )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_turns_reply_to ON chat_turns(reply_to_turn_id)"
        )
        self._migrate_card_settlements_to_wave_2()

    def _migrate_card_settlements_to_wave_2(self) -> None:
        """Rebuild legacy claim/segment receipts into the Wave 2 winner schema."""
        settlement_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(card_settlements)").fetchall()
        }
        target_columns = {
            "ref",
            "verdict",
            "turn_id",
            "payload",
            "applied",
            "result",
            "event_id",
            "created_at",
            "updated_at",
        }
        legacy_columns = {
            "apply_claim_at",
            "apply_claim_token",
            "seg_event",
            "seg_object",
            "seg_marker",
        }
        if settlement_columns == target_columns:
            return

        rows = [dict(row) for row in self.conn.execute("SELECT * FROM card_settlements")]
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute("DROP TABLE IF EXISTS card_settlements_wave_2")
            self.conn.execute(
                """
                CREATE TABLE card_settlements_wave_2 (
                    ref        TEXT PRIMARY KEY,
                    verdict    TEXT NOT NULL,
                    turn_id    TEXT NOT NULL,
                    payload    TEXT NOT NULL DEFAULT '{}',
                    applied    INTEGER NOT NULL DEFAULT 0,
                    result     TEXT NOT NULL DEFAULT '{}',
                    event_id   TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for row in rows:
                normalized_ref = _validated_card_settlement_ref(row.get("ref", ""))
                applied = 1 if int(row.get("applied", 0) or 0) == 1 else 0
                event_was_recorded = applied == 1 or (
                    "seg_event" in settlement_columns and int(row.get("seg_event", 0) or 0) == 1
                )
                payload = row.get("payload", "{}")
                result = row.get("result", "{}")
                created_at = row.get("created_at") or datetime.now(UTC).isoformat()
                updated_at = row.get("updated_at") or created_at
                self.conn.execute(
                    """
                    INSERT INTO card_settlements_wave_2 (
                        ref, verdict, turn_id, payload, applied, result, event_id,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_ref,
                        str(row.get("verdict", "")),
                        str(row.get("turn_id", "")),
                        payload if isinstance(payload, str) else "{}",
                        applied,
                        result if isinstance(result, str) else "{}",
                        (
                            _card_settlement_effect_key(normalized_ref, "event")
                            if event_was_recorded
                            else ""
                        ),
                        created_at,
                        updated_at,
                    ),
                )
            self.conn.execute("DROP TABLE card_settlements")
            self.conn.execute("ALTER TABLE card_settlements_wave_2 RENAME TO card_settlements")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        if legacy_columns.intersection(
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(card_settlements)").fetchall()
        ):
            raise RuntimeError("Legacy card settlement columns survived Wave 2 migration")

    def _ensure_profile_update_ledger_table(self) -> None:
        """Create the append-only profile write-point ledger for existing DBs."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS profile_update_ledger (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                effect_key     TEXT NOT NULL DEFAULT '',
                write_point    TEXT NOT NULL,
                source         TEXT NOT NULL DEFAULT '',
                before_summary TEXT NOT NULL DEFAULT '',
                after_summary  TEXT NOT NULL DEFAULT '',
                diff           TEXT NOT NULL DEFAULT '',
                source_refs    TEXT NOT NULL DEFAULT '',
                outcome        TEXT NOT NULL DEFAULT 'success',
                turn_id        TEXT NOT NULL DEFAULT '',
                gate_verdict   TEXT NOT NULL DEFAULT '',
                held_id        TEXT NOT NULL DEFAULT '',
                error          TEXT NOT NULL DEFAULT ''
            );
        """)
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(profile_update_ledger)").fetchall()
        }
        if "effect_key" not in columns:
            self.conn.execute(
                "ALTER TABLE profile_update_ledger ADD COLUMN effect_key TEXT NOT NULL DEFAULT ''"
            )
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_profile_ledger_timestamp
                ON profile_update_ledger(timestamp);
            CREATE INDEX IF NOT EXISTS idx_profile_ledger_write_point
                ON profile_update_ledger(write_point, timestamp);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_ledger_effect_key
                ON profile_update_ledger(effect_key)
                WHERE effect_key <> '';
        """)

    def _ensure_confusions_table(self) -> None:
        """Create the confusion-object table (Phase 2) for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS confusions (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status                    TEXT NOT NULL DEFAULT 'open',
                source                    TEXT NOT NULL DEFAULT '',
                topic                     TEXT NOT NULL DEFAULT '',
                observation               TEXT NOT NULL DEFAULT '',
                interpretation            TEXT NOT NULL DEFAULT '',
                interpretation_confidence REAL NOT NULL DEFAULT 0.0,
                evidence_refs             TEXT NOT NULL DEFAULT '[]',
                resolution                TEXT NOT NULL DEFAULT '',
                resolution_note           TEXT NOT NULL DEFAULT '',
                asked_at                  TIMESTAMP,
                ask_turn_id               TEXT NOT NULL DEFAULT '',
                defer_count               INTEGER NOT NULL DEFAULT 0,
                expires_at                TIMESTAMP,
                held_updates              TEXT NOT NULL DEFAULT '[]',
                replay_queue              TEXT NOT NULL DEFAULT '[]',
                resolved_at               TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_confusions_clarifying_unique
                ON confusions(status) WHERE status = 'clarifying';
            CREATE INDEX IF NOT EXISTS idx_confusions_status
                ON confusions(status, updated_at);
        """)
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(confusions)").fetchall()
        }
        if "replay_queue" not in columns:
            self.conn.execute(
                "ALTER TABLE confusions ADD COLUMN replay_queue TEXT NOT NULL DEFAULT '[]'"
            )

    def _ensure_watch_later_table(self) -> None:
        """Create the watch_later bookmarks table for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS watch_later (
                bvid     TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note     TEXT DEFAULT '',
                item_key TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_watch_later_added
                ON watch_later(added_at DESC);
        """)
        self._ensure_legacy_saved_item_key_column("watch_later")

    def _ensure_discovery_keywords_table(self) -> None:
        """Create the unified search-keyword store + planner single-flight lock.

        ``discovery_keywords`` is the generation-side cache/history/yield
        ledger for the unified keyword planner (Discover backpressure
        refactor, P1). It carries the same atomic-claim + lease-reclaim
        semantics as the ``xhs_tasks`` / ``dy_tasks`` execution queues
        (``BEGIN IMMEDIATE`` claim, ``pending → claimed`` transition,
        ``claimed_at`` lease), but tracks *which words to search* rather
        than *which tabs to open*.

        The uniqueness constraint is **partial** — it only covers the
        in-flight states (``pending`` / ``claimed`` / ``executing``) so a
        word that has already been ``used`` (or ``expired``) does not block
        the planner from re-generating the same word on a later cycle once
        it has rolled out of the dedup window.

        ``discovery_planner_lock`` is a tiny CAS row used to single-flight
        the planner across loops / restarts. It is held only for *short*
        transactions (acquire → commit → run LLM unlocked → reacquire to
        write), never across the LLM call, so it cannot block other
        SQLite writers.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_keywords (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                platform          TEXT NOT NULL,
                keyword           TEXT NOT NULL,
                keyword_kind      TEXT NOT NULL DEFAULT 'regular',
                profile_kw_digest TEXT NOT NULL DEFAULT '',
                aspect_id         TEXT NOT NULL DEFAULT '',
                inspiration_backend TEXT NOT NULL DEFAULT '',
                inspiration_id    TEXT NOT NULL DEFAULT '',
                inspiration_terms TEXT NOT NULL DEFAULT '',
                expansion_id      TEXT NOT NULL DEFAULT '',
                expansion_label   TEXT NOT NULL DEFAULT '',
                angle_id          TEXT NOT NULL DEFAULT '',
                angle_label       TEXT NOT NULL DEFAULT '',
                query_kind        TEXT NOT NULL DEFAULT '',
                source_domain     TEXT NOT NULL DEFAULT '',
                source_interest   TEXT NOT NULL DEFAULT '',
                generation_reason TEXT NOT NULL DEFAULT '',
                normalized_keyword TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'pending',
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed_at        TIMESTAMP,
                executing_at      TIMESTAMP,
                used_at           TIMESTAMP,
                attempts          INTEGER NOT NULL DEFAULT 0,
                yield_count       INTEGER NOT NULL DEFAULT 0
            );
        """)
        columns = self.conn.execute("PRAGMA table_info(discovery_keywords)").fetchall()
        column_names = {str(row[1]) for row in columns}
        if "keyword_kind" not in column_names:
            self.conn.execute(
                "ALTER TABLE discovery_keywords "
                "ADD COLUMN keyword_kind TEXT NOT NULL DEFAULT 'regular'"
            )
        for name, definition in _DISCOVERY_KEYWORD_METADATA_COLUMNS.items():
            if name not in column_names:
                self.conn.execute(f"ALTER TABLE discovery_keywords ADD COLUMN {name} {definition}")
        self.conn.executescript("""
            -- Partial uniqueness: only the in-flight triplet is unique, so
            -- used/expired history never blocks re-generating the same word.
            DROP INDEX IF EXISTS uq_discovery_keywords_inflight;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_keywords_inflight
                ON discovery_keywords (platform, keyword, profile_kw_digest, keyword_kind)
                WHERE status IN ('pending', 'claimed', 'executing');
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_digest
                ON discovery_keywords (platform, keyword_kind, status, profile_kw_digest);
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_used
                ON discovery_keywords (platform, keyword_kind, status, used_at);

            CREATE TABLE IF NOT EXISTS discovery_planner_lock (
                lock_name    TEXT PRIMARY KEY,
                owner        TEXT NOT NULL DEFAULT '',
                locked_until TIMESTAMP NOT NULL,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- P1.8 yield ledger. One row per (keyword, admitted content) the
            -- keyword produced. The composite primary key makes the yield
            -- backfill idempotent: a retried / out-of-order / duplicate admit
            -- of the SAME (keyword, content) is an INSERT-OR-IGNORE no-op, so
            -- ``discovery_keywords.yield_count`` is only ever bumped once per
            -- distinct produced content. Decoupled from ``used`` (P1.7).
            CREATE TABLE IF NOT EXISTS discovery_keyword_yield (
                keyword_id  INTEGER NOT NULL,
                content_id  TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (keyword_id, content_id)
            );

            CREATE TABLE IF NOT EXISTS discovery_inspiration_probe_cache (
                platform            TEXT NOT NULL,
                profile_kw_digest   TEXT NOT NULL,
                aspect_id           TEXT NOT NULL,
                query_kind          TEXT NOT NULL,
                probe_backend       TEXT NOT NULL DEFAULT 'exa',
                freshness_digest    TEXT NOT NULL DEFAULT '',
                seed_query          TEXT NOT NULL,
                domain_filters_json TEXT NOT NULL DEFAULT '[]',
                inspiration_id      TEXT NOT NULL,
                source_domains_json TEXT NOT NULL DEFAULT '[]',
                source_terms_json   TEXT NOT NULL DEFAULT '[]',
                evidence_titles_json TEXT NOT NULL DEFAULT '[]',
                evidence_urls_json  TEXT NOT NULL DEFAULT '[]',
                reason              TEXT NOT NULL DEFAULT '',
                risk_flags_json     TEXT NOT NULL DEFAULT '[]',
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at          TIMESTAMP,
                selected_count      INTEGER NOT NULL DEFAULT 0,
                yielded_count       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (
                    platform, profile_kw_digest, aspect_id, query_kind, probe_backend,
                    freshness_digest, seed_query, inspiration_id
                )
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_inspiration_probe_lookup
                ON discovery_inspiration_probe_cache (
                    platform, profile_kw_digest, aspect_id, query_kind, created_at
                );

            CREATE TABLE IF NOT EXISTS discovery_inspiration_expansion_cache (
                platform            TEXT NOT NULL,
                profile_kw_digest   TEXT NOT NULL,
                aspect_id           TEXT NOT NULL,
                query_kind          TEXT NOT NULL,
                inspiration_id      TEXT NOT NULL,
                parent_expansion_id TEXT NOT NULL DEFAULT '',
                expansion_id        TEXT NOT NULL,
                hop                 INTEGER NOT NULL DEFAULT 1,
                relation            TEXT NOT NULL DEFAULT '',
                text                TEXT NOT NULL DEFAULT '',
                detail_axes_json    TEXT NOT NULL DEFAULT '[]',
                source_terms_json   TEXT NOT NULL DEFAULT '[]',
                curator_decision    TEXT NOT NULL DEFAULT '',
                curator_score       REAL NOT NULL DEFAULT 0.0,
                curator_reason      TEXT NOT NULL DEFAULT '',
                curator_feedback    TEXT NOT NULL DEFAULT '',
                risk_flags_json     TEXT NOT NULL DEFAULT '[]',
                status              TEXT NOT NULL DEFAULT 'new',
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at          TIMESTAMP,
                last_selected_at    TIMESTAMP,
                selected_count      INTEGER NOT NULL DEFAULT 0,
                realized_count      INTEGER NOT NULL DEFAULT 0,
                yielded_count       INTEGER NOT NULL DEFAULT 0,
                failed_count        INTEGER NOT NULL DEFAULT 0,
                cooldown_until      TIMESTAMP,
                PRIMARY KEY (
                    platform, profile_kw_digest, aspect_id, query_kind,
                    inspiration_id, expansion_id
                )
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_inspiration_expansion_lookup
                ON discovery_inspiration_expansion_cache (
                    platform, profile_kw_digest, aspect_id, inspiration_id, status
                );

            CREATE TABLE IF NOT EXISTS discovery_inspiration_axis (
                axis_id             TEXT PRIMARY KEY,
                interest_label      TEXT NOT NULL,
                interest_id         TEXT,
                axis_label          TEXT NOT NULL,
                axis_kind           TEXT NOT NULL,
                example_terms       TEXT,
                evidence_refs       TEXT,
                source              TEXT NOT NULL,
                time_sensitive      INTEGER NOT NULL DEFAULT 0,
                freshness_ttl_days  INTEGER,
                yield_score         REAL NOT NULL DEFAULT 0.0,
                admissions          INTEGER NOT NULL DEFAULT 0,
                use_count           INTEGER NOT NULL DEFAULT 0,
                status              TEXT NOT NULL DEFAULT 'active',
                created_at          TEXT NOT NULL,
                last_used_at        TEXT,
                last_refreshed_at   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_inspiration_axis_interest
                ON discovery_inspiration_axis (interest_label, status);

            CREATE TABLE IF NOT EXISTS discovery_interest_selection_ledger (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                source_interest     TEXT NOT NULL,
                normalized_interest TEXT NOT NULL,
                query_kind          TEXT NOT NULL DEFAULT '',
                selection_scope     TEXT NOT NULL DEFAULT 'production',
                profile_kw_digest   TEXT NOT NULL DEFAULT '',
                selected_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_discovery_interest_selection_lookup
                ON discovery_interest_selection_ledger (
                    selection_scope, query_kind, normalized_interest, selected_at
                );
        """)
        axis_columns = {
            str(row[1])
            for row in self.conn.execute("PRAGMA table_info(discovery_inspiration_axis)").fetchall()
        }
        for name, definition in _DISCOVERY_INSPIRATION_AXIS_YIELD_COLUMNS.items():
            if name not in axis_columns:
                self.conn.execute(
                    f"ALTER TABLE discovery_inspiration_axis ADD COLUMN {name} {definition}"
                )

    # ── Discovery keyword store (unified search-keyword planner) ──
    #
    # Status machine:
    #   pending → claimed → (inline:    used / failed)
    #                     → (async: executing → used / failed)
    #   any in-flight state → pending (lease reclaim / budget rollback)
    #   pending (stale digest) → expired
    # ``used`` only ever lands at the terminal (never at enqueue time); the
    # word stays "in flight" until its fetch actually completes. yield_count
    # is backfilled later (P1.8) at admission time; P1.1 only stores the column.

    def insert_pending_keywords(
        self,
        platform: str,
        keywords: Sequence[str],
        profile_kw_digest: str,
        *,
        keyword_kind: str = "regular",
        metadata_by_keyword: Mapping[str, Mapping[str, object]] | None = None,
    ) -> int:
        """Batch-insert ``pending`` keywords, ignoring in-flight duplicates.

        The partial unique index ``uq_discovery_keywords_inflight`` means a
        word already ``pending`` / ``claimed`` / ``executing`` for the same
        ``(platform, profile_kw_digest, keyword_kind)`` is silently skipped
        (``OR IGNORE``);
        a word that is only present as ``used`` / ``expired`` history does
        **not** conflict, so the same word can be regenerated. Blank /
        duplicate words within ``keywords`` are de-duplicated up front.

        Returns the number of rows actually inserted.
        """
        platform_key = platform.strip()
        digest = profile_kw_digest.strip()
        kind = _normalize_keyword_kind(keyword_kind)
        seen: set[str] = set()
        metadata_lookup = {
            str(key).strip(): value for key, value in (metadata_by_keyword or {}).items()
        }
        rows: list[tuple[Any, ...]] = []
        for raw in keywords:
            word = str(raw).strip()
            if not word or word in seen:
                continue
            seen.add(word)
            metadata = metadata_lookup.get(word, {})
            rows.append(
                (
                    platform_key,
                    word,
                    kind,
                    digest,
                    _metadata_text(metadata.get("aspect_id")),
                    _metadata_text(metadata.get("inspiration_backend")),
                    _metadata_text(metadata.get("inspiration_id")),
                    _metadata_text(metadata.get("inspiration_terms")),
                    _metadata_text(metadata.get("expansion_id")),
                    _metadata_text(metadata.get("expansion_label")),
                    _metadata_text(metadata.get("angle_id")),
                    _metadata_text(metadata.get("angle_label")),
                    _metadata_text(metadata.get("query_kind") or kind),
                    _metadata_text(metadata.get("source_domain")),
                    _metadata_text(metadata.get("source_interest")),
                    _metadata_text(metadata.get("generation_reason")),
                    _metadata_text(
                        metadata.get("normalized_keyword") or _normalized_keyword_text(word)
                    ),
                    _metadata_text(metadata.get("grounding_source")),
                )
            )
        if not rows:
            return 0
        before = self.conn.total_changes
        self._execute_many_write(
            """
            INSERT OR IGNORE INTO discovery_keywords
                (
                    platform, keyword, keyword_kind, profile_kw_digest,
                    aspect_id, inspiration_backend, inspiration_id, inspiration_terms,
                    expansion_id, expansion_label, angle_id, angle_label, query_kind,
                    source_domain, source_interest, generation_reason, normalized_keyword,
                    grounding_source, status
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            rows,
        )
        return self.conn.total_changes - before

    def record_source_producer_run(self, platform: str, discovered: int) -> None:
        """Stamp one producer round on the shared cadence ledger.

        Only productive rounds are worth recording: a round that came back
        empty should be retried on the next tick, not blocked for a whole
        ``min_interval_minutes``.
        """
        if int(discovered) <= 0:
            return
        key = str(platform or "").strip().lower()
        if not key:
            return
        self.conn.execute(
            "INSERT INTO source_producer_runs(platform, discovered) VALUES (?, ?)",
            (key, max(0, int(discovered))),
        )
        self.conn.commit()

    def source_producer_ran_within(self, platform: str, minutes: int) -> bool:
        """True when this source produced candidates inside the last N minutes.

        Survives a restart, unlike the in-process timestamp this replaced.
        """
        window = int(minutes)
        if window <= 0:
            return False
        key = str(platform or "").strip().lower()
        if not key:
            return False
        row = self.conn.execute(
            """
            SELECT 1 FROM source_producer_runs
            WHERE platform = ? AND created_at >= datetime('now', ?)
            LIMIT 1
            """,
            (key, f"-{window} minutes"),
        ).fetchone()
        return row is not None

    def count_pending_keywords(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        keyword_kind: str = "regular",
    ) -> int:
        """Return how many ``pending`` keywords exist for this digest."""
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM discovery_keywords
            WHERE platform = ?
              AND keyword_kind = ?
              AND status = 'pending'
              AND profile_kw_digest = ?
            """,
            (platform.strip(), kind, profile_kw_digest.strip()),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def count_pending_keywords_all_digests(
        self,
        platform: str,
        *,
        keyword_kind: str = "regular",
    ) -> int:
        """Return reconciled ``pending`` inventory regardless of source digest."""
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM discovery_keywords
            WHERE platform = ?
              AND keyword_kind = ?
              AND status = 'pending'
            """,
            (platform.strip(), kind),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def claim_keywords(
        self,
        platform: str,
        n: int,
        *,
        keyword_kind: str = "regular",
    ) -> list[dict[str, Any]]:
        """Atomically claim up to ``n`` ``pending`` keywords for a platform.

        Uses a short-lived connection + ``BEGIN IMMEDIATE`` so two concurrent
        callers serialize and never receive overlapping rows: the second
        writer blocks until the first commits, after which the just-claimed
        rows are no longer ``pending`` and cannot be re-selected. Mirrors the
        ``xhs_tasks`` / ``dy_tasks`` ``next_pending`` claim, generalized to a
        batch. Returns the claimed rows (``status='claimed'``), oldest first.
        """
        claim_n = max(0, int(n))
        if claim_n <= 0:
            return []
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                """
                SELECT id
                FROM discovery_keywords
                WHERE platform = ?
                  AND keyword_kind = ?
                  AND status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (platform.strip(), kind, claim_n),
            ).fetchall()
            if not pending:
                conn.commit()
                return []
            ids = [int(row["id"]) for row in pending]
            placeholders = ", ".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE discovery_keywords
                SET status = 'claimed', claimed_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND status = 'pending'
                """,
                ids,
            )
            claimed = conn.execute(
                f"""
                SELECT *
                FROM discovery_keywords
                WHERE id IN ({placeholders}) AND status = 'claimed'
                ORDER BY claimed_at ASC, id ASC
                """,
                ids,
            ).fetchall()
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return [dict(row) for row in claimed]

    def mark_keyword_executing(self, keyword_id: int) -> None:
        """Move a ``claimed`` keyword to ``executing`` (async fetch enqueued)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'executing', executing_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_used(self, keyword_id: int) -> None:
        """Mark a keyword ``used`` (terminal — its fetch has completed)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_failed(self, keyword_id: int) -> int:
        """Mark a keyword ``failed`` and bump ``attempts``.

        Returns the new ``attempts`` count so the caller can decide whether
        to retry (re-pend) or treat the word as terminally failed.
        """
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'failed',
                attempts = attempts + 1
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )
        row = self.conn.execute(
            "SELECT attempts FROM discovery_keywords WHERE id = ?",
            (int(keyword_id),),
        ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def rollback_keyword_to_pending(self, keyword_id: int) -> None:
        """Return a ``claimed`` keyword to ``pending`` (budget-rejection rollback).

        Used when a claim succeeded but the downstream enqueue was rejected
        (e.g. daily budget exhausted) so no fetch ever ran — the word must go
        back into the pool rather than be burned. Only ``claimed`` rolls back;
        ``executing`` rows already have an in-flight task and are left alone.
        """
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL
            WHERE id = ? AND status = 'claimed'
            """,
            (int(keyword_id),),
        )

    def requeue_keyword_after_transient_failure(self, keyword_id: int) -> None:
        """Return a claimed/executing keyword after a platform-wide transient.

        Unlike :meth:`mark_keyword_failed`, this does not bump ``attempts``:
        rate limiting says the platform is temporarily unavailable, not that
        the keyword itself failed. Unlike the budget rollback, an async XHS
        task has already reached ``executing``, so both in-flight states are
        eligible here.
        """
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending',
                claimed_at = NULL,
                executing_at = NULL
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def reclaim_leased_keywords(
        self,
        claim_lease_minutes: float,
        executing_timeout_minutes: float,
    ) -> int:
        """Reclaim leaked in-flight keywords back to ``pending``.

        ``claimed`` rows whose ``claimed_at`` is older than
        ``claim_lease_minutes`` (a loop crashed between claim and fetch) and
        ``executing`` rows whose ``executing_at`` is older than
        ``executing_timeout_minutes`` (an async task never reported back) are
        returned to ``pending`` so the word is not lost. Returns the number
        of rows reclaimed.
        """
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        claimed_cutoff = (now - timedelta(minutes=max(0.0, claim_lease_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        executing_cutoff = (now - timedelta(minutes=max(0.0, executing_timeout_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL, executing_at = NULL
            WHERE (status = 'claimed' AND claimed_at IS NOT NULL AND claimed_at <= ?)
               OR (status = 'executing' AND executing_at IS NOT NULL AND executing_at <= ?)
            """,
            (claimed_cutoff, executing_cutoff),
        )
        return int(cursor.rowcount or 0)

    def history_keywords(
        self,
        platform: str,
        window_size: int,
        window_hours: float,
        *,
        keyword_kind: str = "regular",
        include_pending: bool = False,
    ) -> list[str]:
        """Return recent in-flight + consumed keywords for dedup, newest first.

        Includes ``claimed`` / ``executing`` (in-flight, so the planner does
        not regenerate a word a fetch is about to consume) and ``used``
        (recently searched) within the rolling window. With ``include_pending``
        enabled, retained cross-digest inventory also participates in exact /
        family novelty checks before the next generation call. A zero-yield
        ``used`` word retired to ``expired`` keeps its ``used_at`` and remains
        in this cooldown history. Capped at ``window_size`` and bounded to the
        last ``window_hours``. History is scoped by keyword pool so regular
        search and planner-backed explore do not suppress each other's queries.
        """
        from datetime import UTC, datetime, timedelta

        cap = max(0, int(window_size))
        if cap <= 0:
            return []
        kind = _normalize_keyword_kind(keyword_kind)
        self._ensure_fresh_read()
        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, window_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = self.conn.execute(
            """
            SELECT keyword
            FROM discovery_keywords
            WHERE platform = ?
              AND keyword_kind = ?
              AND (
                    status IN ('claimed', 'executing', 'used')
                    OR (? = 1 AND status = 'pending')
                    OR (status = 'expired' AND used_at IS NOT NULL)
                  )
              AND COALESCE(used_at, executing_at, claimed_at, created_at) >= ?
            ORDER BY COALESCE(used_at, executing_at, claimed_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (platform.strip(), kind, int(include_pending), cutoff, cap),
        ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def recycle_oldest_used(
        self,
        platform: str,
        n: int,
        profile_kw_digest: str,
        *,
        keyword_kind: str = "regular",
        min_age_hours: float = 0.0,
    ) -> int:
        """Recycle the oldest ``used`` keywords back to ``pending``.

        Sparse-profile safety valve: when generation can only produce words
        already in history, the planner recycles the least-recently-used words
        so the cache does not starve. Recycled rows are re-stamped with the
        current ``profile_kw_digest`` and become ``pending`` again. Rows that
        would collide with an existing in-flight row (same word already
        pending/claimed/executing for this digest) are skipped to respect the
        partial unique index. Returns the number of rows recycled.
        """
        recycle_n = max(0, int(n))
        if recycle_n <= 0:
            return 0
        from datetime import UTC, datetime, timedelta

        digest = profile_kw_digest.strip()
        kind = _normalize_keyword_kind(keyword_kind)
        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, min_age_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            candidates = conn.execute(
                """
                SELECT id, keyword
                FROM discovery_keywords
                WHERE platform = ?
                  AND keyword_kind = ?
                  AND status = 'used'
                  AND used_at IS NOT NULL
                  AND used_at <= ?
                ORDER BY used_at ASC, id ASC
                """,
                (platform.strip(), kind, cutoff),
            ).fetchall()
            recycled = 0
            for row in candidates:
                if recycled >= recycle_n:
                    break
                clash = conn.execute(
                    """
                    SELECT 1
                    FROM discovery_keywords
                    WHERE platform = ?
                      AND keyword = ?
                      AND profile_kw_digest = ?
                      AND keyword_kind = ?
                      AND status IN ('pending', 'claimed', 'executing')
                    LIMIT 1
                    """,
                    (platform.strip(), str(row["keyword"]), digest, kind),
                ).fetchone()
                if clash is not None:
                    continue
                conn.execute(
                    """
                    UPDATE discovery_keywords
                    SET status = 'pending',
                        profile_kw_digest = ?,
                        claimed_at = NULL,
                        executing_at = NULL,
                        used_at = NULL
                    WHERE id = ? AND status = 'used'
                    """,
                    (digest, int(row["id"])),
                )
                recycled += 1
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return recycled

    def expire_pending_by_digest(self, platform: str, current_digest: str) -> int:
        """Expire ``pending`` keywords generated under a stale profile digest.

        When the profile changes the planner expires any ``pending`` word from
        an older digest so the next generation uses the fresh profile.
        ``used`` / ``claimed`` / ``executing`` rows are left untouched
        (dedup history + in-flight work are preserved). Returns the count
        expired.
        """
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'expired'
            WHERE platform = ? AND status = 'pending' AND profile_kw_digest != ?
            """,
            (platform.strip(), current_digest.strip()),
        )
        return int(cursor.rowcount or 0)

    def reconcile_pending_keyword_digests(
        self,
        platform: str,
        current_digest: str,
        *,
        grace_hours: float,
        max_pending: int,
        blocked_terms: Sequence[str],
        keyword_kind: str = "regular",
    ) -> dict[str, int]:
        """Atomically retain bounded, recent, safe cross-digest inventory.

        Rows keep their original profile digest and generation provenance.
        Only ``pending`` rows in the selected keyword pool participate;
        claimed/executing/terminal rows and the explore pool are untouched.
        The return value is aggregate-only so callers can log reconciliation
        without exposing keyword or profile text.
        """
        from datetime import timedelta

        platform_key = platform.strip()
        digest = current_digest.strip()
        kind = _normalize_keyword_kind(keyword_kind)
        cap = max(0, int(max_pending))
        grace = max(0.0, float(grace_hours))
        cutoff = (datetime.now(UTC) - timedelta(hours=grace)).strftime("%Y-%m-%d %H:%M:%S")
        blocked = tuple(str(term).strip() for term in blocked_terms if str(term).strip())
        ledger = {
            "current": 0,
            "reused": 0,
            "expired_aged": 0,
            "expired_blocked": 0,
            "expired_excess": 0,
        }

        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id, keyword, profile_kw_digest, created_at
                FROM discovery_keywords
                WHERE platform = ?
                  AND keyword_kind = ?
                  AND status = 'pending'
                ORDER BY created_at DESC, id DESC
                """,
                (platform_key, kind),
            ).fetchall()

            current_rows = [row for row in rows if str(row["profile_kw_digest"]) == digest]
            stale_rows = [row for row in rows if str(row["profile_kw_digest"]) != digest]
            expired_ids: list[int] = []
            kept_keys: set[str] = set()

            for row in current_rows:
                keyword_key = _normalized_keyword_text(row["keyword"])
                if ledger["current"] >= cap:
                    ledger["expired_excess"] += 1
                    expired_ids.append(int(row["id"]))
                    continue
                ledger["current"] += 1
                if keyword_key:
                    kept_keys.add(keyword_key)

            for row in stale_rows:
                keyword = str(row["keyword"])
                keyword_key = _normalized_keyword_text(keyword)
                created_at = str(row["created_at"] or "")
                if grace <= 0.0 or not created_at or created_at <= cutoff:
                    ledger["expired_aged"] += 1
                    expired_ids.append(int(row["id"]))
                elif _keyword_matches_blocked_terms(keyword, blocked):
                    ledger["expired_blocked"] += 1
                    expired_ids.append(int(row["id"]))
                elif ledger["current"] + ledger["reused"] >= cap or (
                    keyword_key and keyword_key in kept_keys
                ):
                    ledger["expired_excess"] += 1
                    expired_ids.append(int(row["id"]))
                else:
                    ledger["reused"] += 1
                    if keyword_key:
                        kept_keys.add(keyword_key)

            if expired_ids:
                placeholders = ", ".join("?" for _ in expired_ids)
                conn.execute(
                    f"""
                    UPDATE discovery_keywords
                    SET status = 'expired'
                    WHERE id IN ({placeholders})
                      AND platform = ?
                      AND keyword_kind = ?
                      AND status = 'pending'
                    """,
                    (*expired_ids, platform_key, kind),
                )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return ledger

    def purge_archived_keywords(
        self,
        retention_hours: float,
        *,
        platform: str | None = None,
    ) -> int:
        """Delete archived (``used`` / ``expired`` / ``failed``) rows past retention.

        Cleanup for rows that have left the dedup window and are no longer
        needed for yield accounting. Only terminal-archive states are purged;
        in-flight rows are never deleted. Returns the number of rows removed.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, retention_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        params: list[Any] = [cutoff]
        platform_clause = ""
        if platform is not None:
            platform_clause = " AND platform = ?"
            params.append(platform.strip())
        cursor = self._execute_write(
            f"""
            DELETE FROM discovery_keywords
            WHERE status IN ('used', 'expired', 'failed')
              AND COALESCE(used_at, executing_at, claimed_at, created_at) < ?
              {platform_clause}
            """,
            params,
        )
        return int(cursor.rowcount or 0)

    def record_keyword_interest_selection(
        self,
        source_interests: Sequence[str],
        *,
        query_kind: str = "regular",
        selection_scope: str = "production",
        profile_kw_digest: str = "",
        retention_days: int = 30,
    ) -> int:
        """Record secondary interests sampled for inspiration planning."""

        rows: list[tuple[str, str, str, str, str]] = []
        seen: set[str] = set()
        normalized_query_kind = str(query_kind or "").strip() or "regular"
        normalized_scope = str(selection_scope or "").strip() or "production"
        digest = str(profile_kw_digest or "").strip()
        for raw_label in source_interests:
            label = _display_interest_label(raw_label)
            norm = _normalize_match_text(label)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            rows.append((label, norm, normalized_query_kind, normalized_scope, digest))
        if not rows:
            return 0
        self._execute_many_write(
            """
            INSERT INTO discovery_interest_selection_ledger (
                source_interest, normalized_interest, query_kind,
                selection_scope, profile_kw_digest
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._execute_write(
            """
            DELETE FROM discovery_interest_selection_ledger
            WHERE selected_at < datetime('now', ?)
            """,
            (f"-{max(1, int(retention_days))} days",),
        )
        return len(rows)

    def get_keyword_interest_coverage_snapshot(
        self,
        *,
        limit: int = 200,
        selection_scope: str = "production",
        selection_window_days: int = 14,
    ) -> dict[str, dict[str, object]]:
        """Return coverage counters keyed by keyword ``source_interest``.

        The snapshot intentionally combines generation-side keyword history and
        admitted-pool distribution. Keyword history catches repeated search
        generation even before yield is known; admitted-pool counts cool down
        interests that already dominate the candidate pool. The selection
        ledger cools down interests as soon as they are sampled, even if the
        later search/curation stage produces no keyword rows.
        """

        cap = max(1, int(limit))
        scope = str(selection_scope or "").strip() or "production"
        window_days = max(1, int(selection_window_days))
        self._ensure_fresh_read()
        snapshot: dict[str, dict[str, object]] = defaultdict(_empty_interest_coverage)
        display_by_norm: dict[str, str] = {}

        def bucket_for(raw_label: object) -> tuple[str, dict[str, object]] | None:
            label = _display_interest_label(raw_label)
            norm = _normalize_match_text(label)
            if not norm:
                return None
            display = display_by_norm.setdefault(norm, label)
            return display, snapshot[display]

        rows = self.conn.execute(
            """
            SELECT source_interest,
                   COUNT(*) AS generated_keyword_count,
                   SUM(CASE WHEN status IN ('claimed', 'executing', 'used') THEN 1 ELSE 0 END)
                       AS selected_keyword_count,
                   SUM(COALESCE(yield_count, 0)) AS yield_count,
                   MAX(COALESCE(used_at, executing_at, claimed_at, created_at)) AS last_selected_at
            FROM discovery_keywords
            WHERE COALESCE(source_interest, '') != ''
            GROUP BY source_interest
            ORDER BY generated_keyword_count DESC, source_interest ASC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
        for row in rows:
            bucket_record = bucket_for(row["source_interest"])
            if bucket_record is None:
                continue
            _label, bucket = bucket_record
            bucket["generated_keyword_count"] = _metric_int(
                bucket.get("generated_keyword_count", 0) or 0
            ) + _metric_int(row["generated_keyword_count"] or 0)
            bucket["selected_keyword_count"] = _metric_int(
                bucket.get("selected_keyword_count", 0) or 0
            ) + _metric_int(row["selected_keyword_count"] or 0)
            bucket["yield_count"] = _metric_int(bucket.get("yield_count", 0) or 0) + _metric_int(
                row["yield_count"] or 0
            )
            bucket["last_selected_at"] = str(row["last_selected_at"] or "")

        selection_rows = self.conn.execute(
            """
            SELECT source_interest,
                   normalized_interest,
                   COUNT(*) AS interest_selection_count,
                   MAX(selected_at) AS last_interest_selected_at
            FROM discovery_interest_selection_ledger
            WHERE selection_scope = ?
              AND selected_at >= datetime('now', ?)
            GROUP BY normalized_interest
            ORDER BY interest_selection_count DESC, source_interest ASC
            LIMIT ?
            """,
            (scope, f"-{window_days} days", cap),
        ).fetchall()
        for row in selection_rows:
            bucket_record = bucket_for(row["source_interest"])
            if bucket_record is None:
                continue
            _label, bucket = bucket_record
            bucket["interest_selection_count"] = _metric_int(
                bucket.get("interest_selection_count", 0) or 0
            ) + _metric_int(row["interest_selection_count"] or 0)
            bucket["last_interest_selected_at"] = str(row["last_interest_selected_at"] or "")

        pool_rows = self.conn.execute(
            """
            SELECT COALESCE(NULLIF(pool_topic_label, ''), NULLIF(topic_group, '')) AS interest,
                   COALESCE(content_type, 'video') AS content_type,
                   COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(pool_status, 'fresh') != 'purged_by_dislike'
              AND COALESCE(NULLIF(pool_topic_label, ''), NULLIF(topic_group, '')) IS NOT NULL
            GROUP BY interest, content_type
            ORDER BY n DESC, interest ASC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
        total_admitted = sum(int(row["n"] or 0) for row in pool_rows)
        content_type_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in pool_rows:
            bucket_record = bucket_for(row["interest"])
            if bucket_record is None:
                continue
            label, bucket = bucket_record
            count = int(row["n"] or 0)
            content_type = str(row["content_type"] or "video").strip() or "video"
            bucket["admitted_count"] = int(str(bucket.get("admitted_count") or 0)) + count
            content_type_counts[label][content_type] += count
        for label, counts in content_type_counts.items():
            bucket = snapshot[label]
            admitted_count = int(str(bucket.get("admitted_count") or 0))
            bucket["admitted_share"] = (
                float(admitted_count) / float(total_admitted) if total_admitted > 0 else 0.0
            )
            if counts:
                dominant_type, dominant_count = max(counts.items(), key=lambda item: item[1])
                bucket["dominant_content_type"] = dominant_type
                bucket["dominant_content_type_share"] = (
                    float(dominant_count) / float(admitted_count) if admitted_count > 0 else 0.0
                )
        candidate_rows = self.conn.execute(
            """
            SELECT dc.raw_payload,
                   dc.pool_topic_label,
                   dc.topic_group,
                   dc.topic_key,
                   dc.source_platform,
                   dc.content_type,
                   dk.source_interest AS keyword_source_interest
            FROM discovery_candidates dc
            LEFT JOIN discovery_keywords dk ON dk.id = dc.source_keyword_id
            WHERE COALESCE(dc.status, '') NOT IN ('rejected_duplicate')
            ORDER BY dc.last_seen_at DESC, dc.id DESC
            LIMIT ?
            """,
            (cap * 20,),
        ).fetchall()
        candidate_counts: dict[str, int] = defaultdict(int)
        candidate_platform_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        candidate_type_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in candidate_rows:
            label = str(row["keyword_source_interest"] or "").strip()
            raw_payload = str(row["raw_payload"] or "{}")
            if not label:
                try:
                    payload = json.loads(raw_payload)
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, Mapping):
                    label = str(payload.get("source_interest") or "").strip()
                    nested_metadata = payload.get("metadata")
                    if not label and isinstance(nested_metadata, Mapping):
                        label = str(nested_metadata.get("source_interest") or "").strip()
                    if not label:
                        label = str(payload.get("pool_topic_label") or "").strip()
                    if not label:
                        label = str(payload.get("topic_group") or "").strip()
            if not label:
                label = str(row["pool_topic_label"] or "").strip()
            if not label:
                label = str(row["topic_group"] or "").strip()
            if not label:
                label = str(row["topic_key"] or "").strip()
            bucket_record = bucket_for(label)
            if bucket_record is None:
                continue
            label, _bucket = bucket_record
            platform = str(row["source_platform"] or "").strip() or "unknown"
            content_type = str(row["content_type"] or "").strip() or "unknown"
            candidate_counts[label] += 1
            candidate_platform_counts[label][platform] += 1
            candidate_type_counts[label][content_type] += 1

        total_candidates = sum(candidate_counts.values())
        for label, count in candidate_counts.items():
            bucket = snapshot[label]
            bucket["candidate_count"] = count
            bucket["candidate_share"] = (
                float(count) / float(total_candidates) if total_candidates > 0 else 0.0
            )
            platform_counts = candidate_platform_counts[label]
            if platform_counts:
                dominant_platform, dominant_count = max(
                    platform_counts.items(),
                    key=lambda item: (item[1], item[0]),
                )
                bucket["dominant_candidate_platform"] = dominant_platform
                bucket["dominant_candidate_platform_share"] = (
                    float(dominant_count) / float(count) if count > 0 else 0.0
                )
            type_counts = candidate_type_counts[label]
            if type_counts:
                dominant_type, dominant_count = max(
                    type_counts.items(),
                    key=lambda item: (item[1], item[0]),
                )
                bucket["dominant_candidate_content_type"] = dominant_type
                bucket["dominant_candidate_content_type_share"] = (
                    float(dominant_count) / float(count) if count > 0 else 0.0
                )
        return {label: dict(values) for label, values in snapshot.items()}

    def migrate_keyword_interest_labels(self, mapping: Mapping[str, str]) -> int:
        """Rewrite keyword ``source_interest`` labels after profile consolidation."""

        normalized_mapping: dict[str, str] = {}
        for old, new in mapping.items():
            old_norm = _normalize_match_text(old)
            new_label = _display_interest_label(new)
            if not old_norm or not new_label or old_norm == _normalize_match_text(new_label):
                continue
            normalized_mapping[old_norm] = new_label
        if not normalized_mapping:
            return 0
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT id, source_interest
            FROM discovery_keywords
            WHERE COALESCE(source_interest, '') != ''
            """
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for row in rows:
            current = str(row["source_interest"] or "")
            target = normalized_mapping.get(_normalize_match_text(current), "")
            if target and _display_interest_label(current) != target:
                updates.append((target, int(row["id"])))
        ledger_rows = self.conn.execute(
            """
            SELECT id, source_interest
            FROM discovery_interest_selection_ledger
            WHERE COALESCE(source_interest, '') != ''
            """
        ).fetchall()
        ledger_updates: list[tuple[str, str, int]] = []
        for row in ledger_rows:
            current = str(row["source_interest"] or "")
            target = normalized_mapping.get(_normalize_match_text(current), "")
            if target and _display_interest_label(current) != target:
                ledger_updates.append((target, _normalize_match_text(target), int(row["id"])))
        if updates:
            self._execute_many_write(
                "UPDATE discovery_keywords SET source_interest = ? WHERE id = ?",
                updates,
            )
        if ledger_updates:
            self._execute_many_write(
                """
                UPDATE discovery_interest_selection_ledger
                SET source_interest = ?, normalized_interest = ?
                WHERE id = ?
                """,
                ledger_updates,
            )
        return len(updates) + len(ledger_updates)

    def get_keyword_cohort_stats(self, *, window_days: int = 14) -> dict[str, object]:
        """Compare inspiration and merged keyword cohorts for enablement gating."""

        days = max(1, int(window_days))
        since_modifier = f"-{days} days"
        thresholds = {
            "min_window_days": 14,
            "min_inspiration_claimed_keywords": 200,
            "min_admissions_per_claimed_ratio": 0.8,
            "min_mean_delight_ratio": 0.95,
        }
        cohorts: dict[str, dict[str, object]] = {
            "inspiration": _empty_keyword_cohort(),
            "merged": _empty_keyword_cohort(),
        }
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT CASE WHEN COALESCE(inspiration_id, '') != ''
                        THEN 'inspiration' ELSE 'merged' END AS cohort,
                   COUNT(*) AS generated_keywords,
                   SUM(
                       CASE
                           WHEN status IN ('claimed', 'executing', 'used', 'failed')
                                OR claimed_at IS NOT NULL
                                OR executing_at IS NOT NULL
                                OR used_at IS NOT NULL
                           THEN 1 ELSE 0
                       END
                   ) AS claimed_keywords
            FROM discovery_keywords
            WHERE created_at >= datetime('now', ?)
            GROUP BY cohort
            """,
            (since_modifier,),
        ).fetchall()
        for row in rows:
            cohort = str(row["cohort"] or "")
            if cohort not in cohorts:
                continue
            bucket = cohorts[cohort]
            generated = int(row["generated_keywords"] or 0)
            claimed = int(row["claimed_keywords"] or 0)
            bucket["generated_keywords"] = generated
            bucket["claimed_keywords"] = claimed
            bucket["claimed_rate"] = float(claimed) / float(generated) if generated > 0 else 0.0

        yield_rows = self.conn.execute(
            """
            SELECT CASE WHEN COALESCE(dk.inspiration_id, '') != ''
                        THEN 'inspiration' ELSE 'merged' END AS cohort,
                   COUNT(DISTINCT y.content_id) AS admissions,
                   AVG(COALESCE(c.delight_score, 0.0)) AS mean_delight,
                   COUNT(
                       DISTINCT COALESCE(NULLIF(c.pool_topic_label, ''), NULLIF(c.topic_group, ''))
                   ) AS distinct_topics
            FROM discovery_keyword_yield y
            JOIN discovery_keywords dk ON dk.id = y.keyword_id
            LEFT JOIN content_cache c
              ON c.bvid = y.content_id OR c.content_id = y.content_id
            WHERE y.created_at >= datetime('now', ?)
            GROUP BY cohort
            """,
            (since_modifier,),
        ).fetchall()
        for row in yield_rows:
            cohort = str(row["cohort"] or "")
            if cohort not in cohorts:
                continue
            bucket = cohorts[cohort]
            admissions = int(row["admissions"] or 0)
            claimed = _metric_int(bucket.get("claimed_keywords", 0) or 0)
            distinct_topics = int(row["distinct_topics"] or 0)
            bucket["yield_attributed_admissions"] = admissions
            bucket["admissions_per_claimed_keyword"] = (
                float(admissions) / float(claimed) if claimed > 0 else 0.0
            )
            bucket["mean_delight"] = float(row["mean_delight"] or 0.0)
            bucket["distinct_topics"] = distinct_topics
            bucket["topic_diversity_per_100_admissions"] = (
                float(distinct_topics) * 100.0 / float(admissions) if admissions > 0 else 0.0
            )
        interest_selection: dict[str, dict[str, object]] = {
            "production": _empty_interest_selection_report(),
            "preview": _empty_interest_selection_report(),
        }
        selection_rows = self.conn.execute(
            """
            SELECT selection_scope,
                   source_interest,
                   query_kind,
                   COUNT(*) AS selected_count,
                   MAX(selected_at) AS last_selected_at
            FROM discovery_interest_selection_ledger
            WHERE selected_at >= datetime('now', ?)
            GROUP BY selection_scope, source_interest, query_kind
            ORDER BY selection_scope ASC, selected_count DESC, source_interest ASC
            """,
            (since_modifier,),
        ).fetchall()
        for row in selection_rows:
            scope = str(row["selection_scope"] or "").strip() or "production"
            bucket = interest_selection.setdefault(scope, _empty_interest_selection_report())
            label = _display_interest_label(row["source_interest"])
            query_kind = str(row["query_kind"] or "").strip() or "regular"
            count = _metric_int(row["selected_count"] or 0)
            by_interest = cast("dict[str, int]", bucket["by_source_interest"])
            by_query_kind = cast("dict[str, int]", bucket["by_query_kind"])
            by_interest[label] = by_interest.get(label, 0) + count
            by_query_kind[query_kind] = by_query_kind.get(query_kind, 0) + count
            bucket["total_selected_interests"] = (
                _metric_int(bucket.get("total_selected_interests", 0) or 0) + count
            )
            bucket["distinct_interests"] = len(by_interest)
            current_last = str(bucket.get("last_selected_at") or "")
            row_last = str(row["last_selected_at"] or "")
            if row_last > current_last:
                bucket["last_selected_at"] = row_last
        return {
            "window_days": days,
            "thresholds": thresholds,
            "cohorts": cohorts,
            "interest_selection": interest_selection,
            "gate": _keyword_inspiration_gate(cohorts, thresholds, days),
        }

    # ── Discovery keyword yield (P1.8 admit-time backfill) ───────

    def increment_keyword_yield(self, keyword_id: int, content_id: str) -> bool:
        """Idempotently credit one admitted content to the keyword that produced it.

        Called at admission (the single ``_cache_results`` convergence) for every
        pool item whose ``source_keyword_id`` is set. Idempotency is keyed on
        ``(keyword_id, content_id)`` via the ``discovery_keyword_yield`` ledger:
        the ledger ``INSERT OR IGNORE`` only fires once per distinct produced
        content, so a retried / partial / out-of-order admit of the same item
        does **not** double-count. ``yield_count`` is bumped only on a genuinely
        new ledger row. Decoupled from ``used`` (P1.7) — a word can be ``used``
        and still accrue yield later.

        Returns True if this call recorded a new yield (counter bumped), False
        if it was a duplicate / invalid no-op.
        """
        kid = int(keyword_id)
        cid = str(content_id or "").strip()
        if kid <= 0 or not cid:
            return False
        before = self.conn.total_changes
        self._execute_write(
            """
            INSERT OR IGNORE INTO discovery_keyword_yield (keyword_id, content_id)
            VALUES (?, ?)
            """,
            (kid, cid),
        )
        if self.conn.total_changes == before:
            # Ledger row already existed → this (keyword, content) was already
            # credited. Do not touch the counter.
            return False
        self._execute_write(
            "UPDATE discovery_keywords SET yield_count = yield_count + 1 WHERE id = ?",
            (kid,),
        )
        self._increment_inspiration_yield_for_keyword(kid)
        return True

    def keyword_yield_count(self, keyword_id: int) -> int:
        """Return the stored ``yield_count`` for a keyword (0 if unknown)."""
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT yield_count FROM discovery_keywords WHERE id = ?",
            (int(keyword_id),),
        ).fetchone()
        return int(row["yield_count"]) if row is not None else 0

    def _increment_inspiration_yield_for_keyword(self, keyword_id: int) -> None:
        """Best-effort provenance backfill from keyword yield to inspiration yield."""

        try:
            row = self.conn.execute(
                """
                SELECT platform, profile_kw_digest, keyword_kind, aspect_id, query_kind,
                       inspiration_backend, inspiration_id, expansion_id
                FROM discovery_keywords
                WHERE id = ?
                """,
                (int(keyword_id),),
            ).fetchone()
        except Exception:
            logger.debug("keyword inspiration provenance lookup failed", exc_info=True)
            return
        if row is None:
            return
        platform = str(row["platform"] or "").strip()
        digest = str(row["profile_kw_digest"] or "").strip()
        aspect_id = str(row["aspect_id"] or "").strip()
        query_kind = str(row["query_kind"] or row["keyword_kind"] or "regular").strip()
        backend = str(row["inspiration_backend"] or "exa").strip() or "exa"
        inspiration_id = str(row["inspiration_id"] or "").strip()
        expansion_id = str(row["expansion_id"] or "").strip()
        if not platform or not digest or not aspect_id or not inspiration_id:
            return
        try:
            self._execute_write(
                """
                UPDATE discovery_inspiration_probe_cache
                SET yielded_count = yielded_count + 1
                WHERE platform = ?
                  AND profile_kw_digest = ?
                  AND aspect_id = ?
                  AND query_kind = ?
                  AND probe_backend = ?
                  AND inspiration_id = ?
                """,
                (
                    platform,
                    digest,
                    aspect_id,
                    _normalize_keyword_kind(query_kind),
                    backend,
                    inspiration_id,
                ),
            )
            if expansion_id:
                self._execute_write(
                    """
                    UPDATE discovery_inspiration_expansion_cache
                    SET yielded_count = yielded_count + 1
                    WHERE platform = ?
                      AND profile_kw_digest = ?
                      AND aspect_id = ?
                      AND query_kind = ?
                      AND inspiration_id = ?
                      AND expansion_id = ?
                    """,
                    (
                        platform,
                        digest,
                        aspect_id,
                        _normalize_keyword_kind(query_kind),
                        inspiration_id,
                        expansion_id,
                    ),
                )
        except Exception:
            logger.debug("keyword inspiration yield backfill failed", exc_info=True)

    def keyword_yield_total(self, platform: str) -> int:
        """Return the platform-wide sum of ``yield_count`` across all keywords.

        Cheap single aggregate (the ``(platform, status, …)`` index already
        covers the scan) used only for the planner's per-cycle observability
        ledger (P1.9): the merged LLM call is one ``discovery.keyword_planner``
        caller (token cost can't be split per platform), so the ledger surfaces
        per-platform keyword *production* (generated) + cumulative *yield* so
        operators can still see which platform's search words actually land
        content. Counts every row's stored ``yield_count`` (used / expired
        history included) — it is a running production total, not a live-pool
        gauge. Returns 0 on any error so it never breaks a generation pass.
        """
        try:
            self._ensure_fresh_read()
            row = self.conn.execute(
                "SELECT COALESCE(SUM(yield_count), 0) AS total "
                "FROM discovery_keywords WHERE platform = ?",
                (platform.strip(),),
            ).fetchone()
        except Exception:
            logger.debug("keyword_yield_total failed for %s", platform, exc_info=True)
            return 0
        return int(row["total"]) if row is not None else 0

    # ── Discovery inspiration probe + lateral expansion cache ─────

    def upsert_inspiration_axes(
        self,
        axes: Sequence[AxisRow],
        *,
        bump_usage: bool = True,
    ) -> None:
        """Insert or merge reusable keyword-inspiration axes."""

        affected_interests: set[str] = set()
        for axis in axes:
            if not axis.axis_id or not axis.interest_label or not axis.axis_label:
                continue
            existing = self.conn.execute(
                "SELECT * FROM discovery_inspiration_axis WHERE axis_id = ?",
                (axis.axis_id,),
            ).fetchone()
            last_refreshed_at = axis.last_refreshed_at or axis.created_at
            if existing is None:
                self.conn.execute(
                    """
                    INSERT INTO discovery_inspiration_axis (
                        axis_id, interest_label, interest_id, axis_label, axis_kind,
                        example_terms, evidence_refs, source, time_sensitive,
                        freshness_ttl_days, yield_score, admissions, use_count, status,
                        created_at, last_used_at, last_refreshed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        axis.axis_id,
                        axis.interest_label,
                        axis.interest_id,
                        axis.axis_label,
                        axis.axis_kind,
                        _json_array(axis.example_terms),
                        _json_array(axis.evidence_refs),
                        axis.source,
                        int(axis.time_sensitive),
                        axis.freshness_ttl_days,
                        axis.yield_score,
                        axis.admissions,
                        axis.use_count + (1 if bump_usage else 0),
                        axis.status,
                        axis.created_at,
                        axis.last_used_at,
                        last_refreshed_at,
                    ),
                )
            else:
                # No resurrection: a retired axis keeps merging evidence but its
                # status never flips back — a proven-bad axis must not return via
                # the LLM re-proposing the same label. ``stale`` MAY be revived by
                # a fresh upsert (deliberate asymmetry: a topic can come back).
                existing_status = str(existing["status"] or "active")
                next_status = (
                    existing_status
                    if existing_status == "retired"
                    else (axis.status or existing_status)
                )
                use_count = _metric_int(existing["use_count"]) + (1 if bump_usage else 0)
                last_used_at = (
                    axis.last_used_at or _optional_text(existing["last_used_at"])
                    if bump_usage
                    else _optional_text(existing["last_used_at"])
                )
                self.conn.execute(
                    """
                    UPDATE discovery_inspiration_axis
                    SET interest_label = ?,
                        interest_id = ?,
                        axis_label = ?,
                        axis_kind = ?,
                        example_terms = ?,
                        evidence_refs = ?,
                        source = ?,
                        time_sensitive = ?,
                        freshness_ttl_days = ?,
                        yield_score = ?,
                        admissions = ?,
                        use_count = ?,
                        status = ?,
                        last_used_at = ?,
                        last_refreshed_at = ?
                    WHERE axis_id = ?
                    """,
                    (
                        axis.interest_label,
                        axis.interest_id or str(existing["interest_id"] or ""),
                        axis.axis_label,
                        axis.axis_kind,
                        _json_array_union(existing["example_terms"], axis.example_terms),
                        _json_array_union(existing["evidence_refs"], axis.evidence_refs),
                        axis.source or str(existing["source"] or ""),
                        int(axis.time_sensitive),
                        axis.freshness_ttl_days,
                        max(_metric_float(existing["yield_score"]), axis.yield_score),
                        max(_metric_int(existing["admissions"]), axis.admissions),
                        use_count,
                        next_status,
                        last_used_at,
                        last_refreshed_at,
                        axis.axis_id,
                    ),
                )
            affected_interests.add(axis.interest_label)
        self.conn.commit()
        for interest_label in sorted(affected_interests):
            self._enforce_inspiration_axis_active_cap(interest_label)

    def backfill_inspiration_axis_yield(
        self,
        *,
        window_days: int = 30,
        now: datetime,
    ) -> dict[str, int]:
        """Recompute per-axis yield stats over a trailing window (SET, not add).

        This is a full recompute with SET semantics, so it is idempotent by
        construction: the same input rows always produce the same table state,
        no watermark / dedup bookkeeping. Old admissions decay naturally as they
        slide out of the trailing window (successes must stay fresh — a feature).

        Attribution rides the persisted ``angle_id`` / ``angle_label`` columns of
        ``discovery_keywords`` (no keyword-schema change): a row is credited to an
        axis via ``angle_id`` ONLY when that id is a real axis; otherwise the id
        is re-derived from ``source_interest`` + ``angle_label`` — the same stable
        hash the axis stores. The existence check prevents a legacy row whose
        label happens to start with ``axis:`` from being mistaken for a real id.

        For every axis the yield fields are SET (even to zero) so an axis with no
        window rows lands at ``window_uses=0`` / ``admissions=0`` /
        ``yield_score = prior`` — smooth and continuous with the prior floor.
        """

        from datetime import timedelta

        window = max(1, int(window_days))
        now_utc = _axis_now_utc(now)
        since = (now_utc - timedelta(days=window)).strftime("%Y-%m-%d %H:%M:%S")
        backfilled_at = now_utc.isoformat()
        prior = _INSPIRATION_AXIS_EXPLORATION_PRIOR

        axis_rows = self.conn.execute("SELECT axis_id FROM discovery_inspiration_axis").fetchall()
        known_axis_ids = {str(row["axis_id"]) for row in axis_rows}

        uses: dict[str, int] = {}
        admissions: dict[str, int] = {}
        keyword_rows = self.conn.execute(
            """
            SELECT angle_id, angle_label, source_interest, status,
                   COALESCE(yield_count, 0) AS yield_count
            FROM discovery_keywords
            WHERE created_at >= ?
              AND (COALESCE(angle_id, '') != '' OR COALESCE(angle_label, '') != '')
            """,
            (since,),
        ).fetchall()
        for row in keyword_rows:
            axis_id = _attribute_inspiration_axis_id(
                angle_id=str(row["angle_id"] or ""),
                source_interest=str(row["source_interest"] or ""),
                angle_label=str(row["angle_label"] or ""),
                known_axis_ids=known_axis_ids,
            )
            if axis_id is None or axis_id not in known_axis_ids:
                continue
            admissions[axis_id] = admissions.get(axis_id, 0) + _metric_int(row["yield_count"])
            if str(row["status"] or "") in _INSPIRATION_CONSUMED_KEYWORD_STATUSES:
                uses[axis_id] = uses.get(axis_id, 0) + 1

        for row in axis_rows:
            axis_id = str(row["axis_id"])
            window_uses = uses.get(axis_id, 0)
            axis_admissions = admissions.get(axis_id, 0)
            yield_score = (axis_admissions + prior) / (window_uses + 1.0)
            self.conn.execute(
                """
                UPDATE discovery_inspiration_axis
                SET window_uses = ?,
                    admissions = ?,
                    yield_score = ?,
                    yield_backfilled_at = ?
                WHERE axis_id = ?
                """,
                (window_uses, axis_admissions, yield_score, backfilled_at, axis_id),
            )
        self.conn.commit()
        return {
            "axes": len(axis_rows),
            "attributed_axes": len(set(uses) | set(admissions)),
            "window_days": window,
        }

    def apply_inspiration_axis_lifecycle(self, *, now: datetime) -> dict[str, int]:
        """Apply the deterministic axis lifecycle transitions (post-backfill).

        Three transitions, in order, all keyed on the injected ``now``:

        1. ``time_sensitive`` axes past their ``freshness_ttl_days`` →
           persisted ``status='stale'`` (Phase 1 only filtered them at read
           time).
        2. Active axes given ≥ ``_INSPIRATION_AXIS_RETIRE_MIN_WINDOW_USES``
           consumption chances whose post-backfill ``yield_score`` stays below
           ``_INSPIRATION_AXIS_RETIRE_YIELD_SCORE`` → ``status='retired'``.
           Retired axes never re-enter selection and are not resurrected by
           upsert.
        3. Stale/retired rows whose ``last_refreshed_at`` is older than
           ``_INSPIRATION_AXIS_PURGE_AFTER_DAYS`` days → physical DELETE.

        Returns a ``{"staled": n, "retired": n, "purged": n}`` transition
        summary for stage telemetry.
        """

        from datetime import timedelta

        now_utc = _axis_now_utc(now)
        purge_cutoff = now_utc - timedelta(days=_INSPIRATION_AXIS_PURGE_AFTER_DAYS)

        staled_ids: list[str] = []
        active_rows = self.conn.execute(
            "SELECT * FROM discovery_inspiration_axis WHERE status = 'active'"
        ).fetchall()
        for row in active_rows:
            if _axis_is_time_expired(row, now_utc):
                staled_ids.append(str(row["axis_id"]))
        if staled_ids:
            self.conn.executemany(
                "UPDATE discovery_inspiration_axis SET status = 'stale' WHERE axis_id = ?",
                [(axis_id,) for axis_id in staled_ids],
            )

        retired = self.conn.execute(
            """
            UPDATE discovery_inspiration_axis
            SET status = 'retired'
            WHERE status = 'active'
              AND window_uses >= ?
              AND yield_score < ?
            """,
            (
                _INSPIRATION_AXIS_RETIRE_MIN_WINDOW_USES,
                _INSPIRATION_AXIS_RETIRE_YIELD_SCORE,
            ),
        ).rowcount

        purged_ids: list[str] = []
        inactive_rows = self.conn.execute(
            "SELECT axis_id, last_refreshed_at FROM discovery_inspiration_axis "
            "WHERE status IN ('stale', 'retired')"
        ).fetchall()
        for row in inactive_rows:
            refreshed_at = _parse_axis_datetime(row["last_refreshed_at"])
            if refreshed_at is not None and refreshed_at < purge_cutoff:
                purged_ids.append(str(row["axis_id"]))
        if purged_ids:
            self.conn.executemany(
                "DELETE FROM discovery_inspiration_axis WHERE axis_id = ?",
                [(axis_id,) for axis_id in purged_ids],
            )

        self.conn.commit()
        return {
            "staled": len(staled_ids),
            "retired": max(0, int(retired)),
            "purged": len(purged_ids),
        }

    def list_inspiration_axes(
        self,
        interest_labels: Sequence[str],
        *,
        limit: int,
        now: datetime,
    ) -> list[AxisRow]:
        """Return active reusable inspiration axes, ranked with a zero-yield prior."""

        labels = _unique_clean_strings(interest_labels)
        per_interest_limit = max(0, int(limit))
        if not labels or per_interest_limit <= 0:
            return []
        placeholders = ", ".join("?" for _ in labels)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM discovery_inspiration_axis
            WHERE status = 'active'
              AND interest_label IN ({placeholders})
            """,
            tuple(labels),
        ).fetchall()
        by_interest: dict[str, list[sqlite3.Row]] = {label: [] for label in labels}
        for row in rows:
            if _axis_is_time_expired(row, now):
                continue
            by_interest.setdefault(str(row["interest_label"]), []).append(row)

        result: list[AxisRow] = []
        for label in labels:
            ranked = sorted(
                by_interest.get(label, []), key=lambda row: _axis_list_sort_key(row, now)
            )
            result.extend(
                self._row_to_discovery_inspiration_axis(row) for row in ranked[:per_interest_limit]
            )
        return result

    def list_inspiration_axes_by_source(
        self,
        source: str,
        *,
        min_yield: float = 0.0,
        limit: int,
        now: datetime,
    ) -> list[AxisRow]:
        """Return active axes filtered by ``source`` (Phase 2.3, E5).

        Explore axes carry cross-domain ``interest_label``s that never match a
        selected like interest, so :meth:`list_inspiration_axes` (interest-keyed)
        cannot surface them. This mirrors that DAO's ``status='active'`` filter,
        the SAME ``_axis_is_time_expired`` time-sensitive suppression, and the
        SAME Phase-2 ``_axis_list_sort_key`` ordering (freshness × conditional
        prior floor), but keys on ``source`` and applies a raw ``yield_score >=
        min_yield`` floor — letting the explore stage reuse its own high-yield
        cross-domain axes. ``limit`` is a global (not per-interest) bound.
        """

        source_key = str(source or "").strip()
        bounded_limit = max(0, int(limit))
        if not source_key or bounded_limit <= 0:
            return []
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT *
            FROM discovery_inspiration_axis
            WHERE status = 'active'
              AND source = ?
              AND yield_score >= ?
            """,
            (source_key, float(min_yield)),
        ).fetchall()
        ranked = sorted(
            (row for row in rows if not _axis_is_time_expired(row, now)),
            key=lambda row: _axis_list_sort_key(row, now),
        )
        return [self._row_to_discovery_inspiration_axis(row) for row in ranked[:bounded_limit]]

    def _enforce_inspiration_axis_active_cap(self, interest_label: str) -> None:
        rows = self.conn.execute(
            """
            SELECT *
            FROM discovery_inspiration_axis
            WHERE interest_label = ?
              AND status = 'active'
            """,
            (interest_label,),
        ).fetchall()
        if len(rows) <= _INSPIRATION_AXIS_ACTIVE_CAP:
            return
        ranked = sorted(rows, key=_axis_cap_sort_key)
        stale_ids = [str(row["axis_id"]) for row in ranked[_INSPIRATION_AXIS_ACTIVE_CAP:]]
        self.conn.executemany(
            "UPDATE discovery_inspiration_axis SET status = 'stale' WHERE axis_id = ?",
            [(axis_id,) for axis_id in stale_ids],
        )
        self.conn.commit()

    def upsert_discovery_inspiration_seed(
        self,
        *,
        platform: str,
        profile_kw_digest: str,
        aspect_id: str,
        query_kind: str,
        seed_query: str,
        inspiration_id: str,
        source_terms: Sequence[object] | None = None,
        evidence_titles: Sequence[object] | None = None,
        evidence_urls: Sequence[object] | None = None,
        reason: str = "",
        risk_flags: Sequence[object] | None = None,
        probe_backend: str = "exa",
        freshness_digest: str = "",
        domain_filters: Sequence[object] | None = None,
        source_domains: Sequence[object] | None = None,
        expires_at: str | None = None,
    ) -> None:
        """Insert or refresh one search-derived inspiration seed."""

        self._execute_write(
            """
            INSERT INTO discovery_inspiration_probe_cache (
                platform, profile_kw_digest, aspect_id, query_kind, probe_backend,
                freshness_digest, seed_query, domain_filters_json, inspiration_id,
                source_domains_json, source_terms_json, evidence_titles_json,
                evidence_urls_json, reason, risk_flags_json, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                platform, profile_kw_digest, aspect_id, query_kind, probe_backend,
                freshness_digest, seed_query, inspiration_id
            ) DO UPDATE SET
                domain_filters_json = excluded.domain_filters_json,
                source_domains_json = excluded.source_domains_json,
                source_terms_json = excluded.source_terms_json,
                evidence_titles_json = excluded.evidence_titles_json,
                evidence_urls_json = excluded.evidence_urls_json,
                reason = excluded.reason,
                risk_flags_json = excluded.risk_flags_json,
                expires_at = excluded.expires_at
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                probe_backend.strip() or "exa",
                freshness_digest.strip(),
                seed_query.strip(),
                _json_array(domain_filters),
                inspiration_id.strip(),
                _json_array(source_domains),
                _json_array(source_terms),
                _json_array(evidence_titles),
                _json_array(evidence_urls),
                reason.strip(),
                _json_array(risk_flags),
                expires_at,
            ),
        )

    def list_discovery_inspiration_seeds(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str | None = None,
        query_kind: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return cached inspiration seeds for a profile digest."""

        clauses = ["platform = ?", "profile_kw_digest = ?"]
        params: list[Any] = [platform.strip(), profile_kw_digest.strip()]
        if aspect_id is not None:
            clauses.append("aspect_id = ?")
            params.append(aspect_id.strip())
        if query_kind is not None:
            clauses.append("query_kind = ?")
            params.append(_normalize_keyword_kind(query_kind))
        sql = f"""
            SELECT *
            FROM discovery_inspiration_probe_cache
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at ASC, seed_query ASC, inspiration_id ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        self._ensure_fresh_read()
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_discovery_inspiration_seed(row) for row in rows]

    def increment_discovery_inspiration_yield(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str,
        query_kind: str,
        seed_query: str,
        inspiration_id: str,
        probe_backend: str = "exa",
        freshness_digest: str = "",
        source_terms: Sequence[object] | None = None,
    ) -> bool:
        """Bump the yield counter for one cached inspiration seed."""

        _ = source_terms
        cursor = self._execute_write(
            """
            UPDATE discovery_inspiration_probe_cache
            SET yielded_count = yielded_count + 1
            WHERE platform = ?
              AND profile_kw_digest = ?
              AND aspect_id = ?
              AND query_kind = ?
              AND probe_backend = ?
              AND freshness_digest = ?
              AND seed_query = ?
              AND inspiration_id = ?
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                probe_backend.strip() or "exa",
                freshness_digest.strip(),
                seed_query.strip(),
                inspiration_id.strip(),
            ),
        )
        return int(cursor.rowcount or 0) > 0

    def upsert_discovery_inspiration_expansion(
        self,
        *,
        platform: str,
        profile_kw_digest: str,
        aspect_id: str,
        query_kind: str,
        inspiration_id: str,
        expansion_id: str,
        parent_expansion_id: str = "",
        hop: int = 1,
        relation: str = "",
        text: str = "",
        detail_axes: Sequence[object] | None = None,
        source_terms: Sequence[object] | None = None,
        curator_decision: str = "",
        curator_score: float = 0.0,
        curator_reason: str = "",
        curator_feedback: str = "",
        risk_flags: Sequence[object] | None = None,
        status: str = "new",
        expires_at: str | None = None,
        cooldown_until: str | None = None,
    ) -> None:
        """Insert or refresh one lateral expansion under an inspiration seed."""

        self._execute_write(
            """
            INSERT INTO discovery_inspiration_expansion_cache (
                platform, profile_kw_digest, aspect_id, query_kind, inspiration_id,
                parent_expansion_id, expansion_id, hop, relation, text,
                detail_axes_json, source_terms_json, curator_decision, curator_score,
                curator_reason, curator_feedback, risk_flags_json, status, expires_at,
                cooldown_until
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                platform, profile_kw_digest, aspect_id, query_kind,
                inspiration_id, expansion_id
            ) DO UPDATE SET
                parent_expansion_id = excluded.parent_expansion_id,
                hop = excluded.hop,
                relation = excluded.relation,
                text = excluded.text,
                detail_axes_json = excluded.detail_axes_json,
                source_terms_json = excluded.source_terms_json,
                curator_decision = excluded.curator_decision,
                curator_score = excluded.curator_score,
                curator_reason = excluded.curator_reason,
                curator_feedback = excluded.curator_feedback,
                risk_flags_json = excluded.risk_flags_json,
                status = excluded.status,
                expires_at = excluded.expires_at,
                cooldown_until = excluded.cooldown_until
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                inspiration_id.strip(),
                parent_expansion_id.strip(),
                expansion_id.strip(),
                max(1, int(hop)),
                relation.strip(),
                text.strip(),
                _json_array(detail_axes),
                _json_array(source_terms),
                curator_decision.strip(),
                float(curator_score),
                curator_reason.strip(),
                curator_feedback.strip(),
                _json_array(risk_flags),
                status.strip() or "new",
                expires_at,
                cooldown_until,
            ),
        )

    def list_discovery_inspiration_expansions(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str | None = None,
        inspiration_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return cached lateral expansions for a profile digest."""

        clauses = ["platform = ?", "profile_kw_digest = ?"]
        params: list[Any] = [platform.strip(), profile_kw_digest.strip()]
        if aspect_id is not None:
            clauses.append("aspect_id = ?")
            params.append(aspect_id.strip())
        if inspiration_id is not None:
            clauses.append("inspiration_id = ?")
            params.append(inspiration_id.strip())
        if status is not None:
            clauses.append("status = ?")
            params.append(status.strip())
        sql = f"""
            SELECT *
            FROM discovery_inspiration_expansion_cache
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at ASC, inspiration_id ASC, expansion_id ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        self._ensure_fresh_read()
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_discovery_inspiration_expansion(row) for row in rows]

    def increment_discovery_inspiration_expansion_yield(
        self,
        platform: str,
        profile_kw_digest: str,
        *,
        aspect_id: str,
        query_kind: str,
        inspiration_id: str,
        expansion_id: str,
    ) -> bool:
        """Bump the yield counter for one cached lateral expansion."""

        cursor = self._execute_write(
            """
            UPDATE discovery_inspiration_expansion_cache
            SET yielded_count = yielded_count + 1
            WHERE platform = ?
              AND profile_kw_digest = ?
              AND aspect_id = ?
              AND query_kind = ?
              AND inspiration_id = ?
              AND expansion_id = ?
            """,
            (
                platform.strip(),
                profile_kw_digest.strip(),
                aspect_id.strip(),
                _normalize_keyword_kind(query_kind),
                inspiration_id.strip(),
                expansion_id.strip(),
            ),
        )
        return int(cursor.rowcount or 0) > 0

    @staticmethod
    def _row_to_discovery_inspiration_axis(row: sqlite3.Row) -> AxisRow:
        ttl_value = row["freshness_ttl_days"]
        return AxisRow(
            axis_id=str(row["axis_id"]),
            interest_label=str(row["interest_label"]),
            interest_id=str(row["interest_id"] or ""),
            axis_label=str(row["axis_label"]),
            axis_kind=str(row["axis_kind"]),
            example_terms=tuple(_load_json_array(row["example_terms"])),
            evidence_refs=tuple(_load_json_array(row["evidence_refs"])),
            source=str(row["source"]),
            time_sensitive=bool(_metric_int(row["time_sensitive"])),
            freshness_ttl_days=None if ttl_value is None else _metric_int(ttl_value),
            yield_score=_metric_float(row["yield_score"]),
            admissions=_metric_int(row["admissions"]),
            use_count=_metric_int(row["use_count"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            last_used_at=_optional_text(row["last_used_at"]),
            last_refreshed_at=_optional_text(row["last_refreshed_at"]),
        )

    @staticmethod
    def _row_to_discovery_inspiration_seed(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "platform": str(row["platform"]),
            "profile_kw_digest": str(row["profile_kw_digest"]),
            "aspect_id": str(row["aspect_id"]),
            "query_kind": str(row["query_kind"]),
            "probe_backend": str(row["probe_backend"]),
            "freshness_digest": str(row["freshness_digest"]),
            "seed_query": str(row["seed_query"]),
            "domain_filters": _load_json_array(row["domain_filters_json"]),
            "inspiration_id": str(row["inspiration_id"]),
            "source_domains": _load_json_array(row["source_domains_json"]),
            "source_terms": _load_json_array(row["source_terms_json"]),
            "evidence_titles": _load_json_array(row["evidence_titles_json"]),
            "evidence_urls": _load_json_array(row["evidence_urls_json"]),
            "reason": str(row["reason"]),
            "risk_flags": _load_json_array(row["risk_flags_json"]),
            "selected_count": int(row["selected_count"]),
            "yielded_count": int(row["yielded_count"]),
        }

    @staticmethod
    def _row_to_discovery_inspiration_expansion(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "platform": str(row["platform"]),
            "profile_kw_digest": str(row["profile_kw_digest"]),
            "aspect_id": str(row["aspect_id"]),
            "query_kind": str(row["query_kind"]),
            "inspiration_id": str(row["inspiration_id"]),
            "parent_expansion_id": str(row["parent_expansion_id"]),
            "expansion_id": str(row["expansion_id"]),
            "hop": int(row["hop"]),
            "relation": str(row["relation"]),
            "text": str(row["text"]),
            "detail_axes": _load_json_array(row["detail_axes_json"]),
            "source_terms": _load_json_array(row["source_terms_json"]),
            "curator_decision": str(row["curator_decision"]),
            "curator_score": float(row["curator_score"]),
            "curator_reason": str(row["curator_reason"]),
            "curator_feedback": str(row["curator_feedback"]),
            "risk_flags": _load_json_array(row["risk_flags_json"]),
            "status": str(row["status"]),
            "selected_count": int(row["selected_count"]),
            "realized_count": int(row["realized_count"]),
            "yielded_count": int(row["yielded_count"]),
            "failed_count": int(row["failed_count"]),
        }

    def used_keyword_count(self, platform: str) -> int:
        """Count ``used`` keywords for a platform (P3.2 dynamic-cap denominator).

        Paired with :meth:`keyword_yield_total` to derive the platform's observed
        average yield-per-keyword (total yield / used count). Cheap single
        aggregate; returns 0 on any error so it never breaks a generation pass.
        """
        try:
            self._ensure_fresh_read()
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM discovery_keywords "
                "WHERE platform = ? AND status = 'used'",
                (platform.strip(),),
            ).fetchone()
        except Exception:
            logger.debug("used_keyword_count failed for %s", platform, exc_info=True)
            return 0
        return int(row["n"]) if row is not None else 0

    def retire_zero_yield_keywords(
        self,
        platform: str,
        *,
        min_age_minutes: float = 60.0,
    ) -> int:
        """Retire ``used`` words that have produced nothing, conservatively.

        A word that has been ``used`` for at least ``min_age_minutes`` and still
        has ``yield_count == 0`` is moved to ``expired`` so the recycler does not
        keep re-pending a search term that demonstrably never lands content.

        The age floor is the safety valve against retiring a *freshly* used word
        whose admit is still pending: inline-admit credits yield synchronously,
        but fetch-only (X / YouTube) and async (XHS) words are marked ``used`` at
        handoff and only accrue yield once the shared pipeline admits — minutes
        later. ``min_age_minutes`` must comfortably exceed that admit latency.
        Only ``used`` rows are touched; in-flight / pending / already-expired
        rows are left alone. Returns the number of rows retired.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(minutes=max(0.0, min_age_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'expired'
            WHERE platform = ?
              AND status = 'used'
              AND yield_count = 0
              AND used_at IS NOT NULL
              AND used_at <= ?
            """,
            (platform.strip(), cutoff),
        )
        return int(cursor.rowcount or 0)

    def retire_duplicate_only_keywords(self, keyword_ids: Sequence[int]) -> int:
        """Retire searches whose returned candidates were all already known.

        Candidate prefiltering is the earliest point where the system knows a
        successful platform fetch added no new identity. Persist that signal
        immediately instead of waiting for the generic zero-yield age floor.
        ``used_at`` is retained/filled so the word stays in cooldown history and
        cannot be regenerated by the same stable-profile plan.
        """
        ids = sorted({int(item) for item in keyword_ids if int(item) > 0})
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        cursor = self._execute_write(
            f"""
            UPDATE discovery_keywords
            SET status = 'expired',
                used_at = COALESCE(used_at, CURRENT_TIMESTAMP)
            WHERE id IN ({placeholders})
              AND status IN ('claimed', 'executing', 'used')
              AND yield_count = 0
            """,
            ids,
        )
        return int(cursor.rowcount or 0)

    # ── Discovery keyword planner single-flight lock ─────────────

    def acquire_planner_lock(self, owner: str, lease_seconds: float) -> bool:
        """Try to acquire the planner single-flight lock via CAS.

        ``BEGIN IMMEDIATE`` serializes the check-and-set: the lock is granted
        if it is unheld, already owned by ``owner``, or its ``locked_until``
        has elapsed (the previous holder crashed). On success ``locked_until``
        is extended by ``lease_seconds`` and the row's ``owner`` is set.
        **Short transaction only** — acquire, commit, then run the LLM call
        *without* holding any DB lock; reacquire/``renew`` to write results.
        Returns True if the lock is now held by ``owner``.
        """
        from datetime import UTC, datetime, timedelta

        lock_name = "keyword_planner"
        now = datetime.now(UTC)
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        new_until = (now + timedelta(seconds=max(0.0, lease_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, locked_until FROM discovery_planner_lock WHERE lock_name = ?",
                (lock_name,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO discovery_planner_lock
                        (lock_name, owner, locked_until, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (lock_name, owner, new_until),
                )
                conn.commit()
                return True
            held_by = str(row["owner"] or "")
            locked_until = str(row["locked_until"] or "")
            if held_by and held_by != owner and locked_until > now_text:
                # Still validly held by someone else.
                conn.commit()
                return False
            conn.execute(
                """
                UPDATE discovery_planner_lock
                SET owner = ?, locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE lock_name = ?
                """,
                (owner, new_until, lock_name),
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return True

    def renew_planner_lock(self, owner: str, lease_seconds: float) -> bool:
        """Extend the planner lock lease if still owned by ``owner``.

        Returns True if the lease was extended, False if the lock has been
        taken over by another owner in the meantime.
        """
        from datetime import UTC, datetime, timedelta

        new_until = (datetime.now(UTC) + timedelta(seconds=max(0.0, lease_seconds))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_planner_lock
            SET locked_until = ?, updated_at = CURRENT_TIMESTAMP
            WHERE lock_name = 'keyword_planner' AND owner = ?
            """,
            (new_until, owner),
        )
        return int(cursor.rowcount or 0) > 0

    def release_planner_lock(self, owner: str) -> bool:
        """Release the planner lock if still owned by ``owner``.

        Clears the owner and expires ``locked_until`` so the next acquirer
        can take it immediately. Returns True if a row was released.
        """
        from datetime import UTC, datetime

        now_text = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._execute_write(
            """
            UPDATE discovery_planner_lock
            SET owner = '', locked_until = ?, updated_at = CURRENT_TIMESTAMP
            WHERE lock_name = 'keyword_planner' AND owner = ?
            """,
            (now_text, owner),
        )
        return int(cursor.rowcount or 0) > 0

    # ── Watch-later CRUD ─────────────────────────────────────────

    def add_to_watch_later(self, bvid: str, note: str = "") -> bool:
        """Bookmark a video. Returns True if newly inserted, False if updated."""
        item = self._bilibili_saved_item_input(bvid)
        self.upsert_saved_membership("watch_later", item, note)
        self._execute_write(
            """
            INSERT INTO watch_later (bvid, note, item_key)
            VALUES (?, ?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note,
                item_key = excluded.item_key
            """,
            (bvid.strip(), note, item.item_key),
        )
        return self.conn.total_changes > 0

    def remove_from_watch_later(self, bvid: str) -> bool:
        """Remove a bookmark. Returns True if a row was deleted."""
        item_key = self._resolve_legacy_saved_item_key("watch_later", bvid)
        if item_key is None:
            return False
        return self.remove_saved_membership("watch_later", item_key)

    def is_in_watch_later(self, bvid: str) -> bool:
        """Check whether a video is bookmarked."""
        item_key = self._resolve_legacy_saved_item_key("watch_later", bvid)
        return (
            item_key is not None and self.get_saved_membership("watch_later", item_key) is not None
        )

    def count_watch_later(self) -> int:
        """Return total number of bookmarked videos."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM saved_memberships WHERE list_kind = ?",
            ("watch_later",),
        ).fetchone()
        return int(row[0]) if row else 0

    def list_watch_later(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return bookmarked videos with content_cache metadata, newest first."""
        return [
            {
                "bvid": row["content_id"],
                "item_key": row["item_key"],
                "content_id": row["content_id"],
                "added_at": row["added_at"],
                "note": row["note"],
                "title": row["title"],
                "up_name": row["author_name"],
                "cover_url": row["cover_url"],
                "content_url": row["content_url"],
                "source_platform": row["source_platform"],
                "content_type": row["content_type"],
            }
            for row in self.list_saved_memberships("watch_later", limit, offset)
        ]

    def _ensure_favorites_table(self) -> None:
        """Create the favorites (收藏夹) table for existing databases.

        Favorites are a permanent, curated keep — distinct from the
        ephemeral ``watch_later`` queue. The two tables are independent so
        a video can be in one, both, or neither.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS favorites (
                bvid     TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note     TEXT DEFAULT '',
                item_key TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_favorites_added
                ON favorites(added_at DESC);
        """)
        self._ensure_legacy_saved_item_key_column("favorites")

    def _ensure_legacy_saved_item_key_column(self, table_name: str) -> None:
        """Add the stable normalized identity link to a trusted legacy saved table."""
        if table_name not in {"watch_later", "favorites"}:
            raise ValueError(f"unsupported legacy saved table: {table_name}")
        columns = {
            str(row["name"])
            for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if "item_key" not in columns:
            self.conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN item_key TEXT NOT NULL DEFAULT ''"
            )

    def _ensure_user_visual_clusters_table(self) -> None:
        """Create the user visual-profile centroids table.

        Stores k mean centroids per polarity (pos/neg) built from the user's
        liked/disliked cover embeddings (see
        ``recommendation.visual_profile.build_centroids``). Single-user model:
        the table is implicitly scoped to the one user, like ``events`` /
        ``recommendations``. Centroids live in the main DB (not
        ``embedding_cache.db``) because they are profile-scoped, not
        content-scoped, and the embedding cache exposes only get/put/count.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_visual_clusters (
                cluster_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                polarity     TEXT NOT NULL,        -- 'pos' | 'neg'
                centroid     TEXT NOT NULL,        -- JSON list[float]
                member_count INTEGER NOT NULL DEFAULT 0,
                embedding_fingerprint TEXT NOT NULL DEFAULT '',
                embedding_dimension INTEGER NOT NULL DEFAULT 0,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_user_visual_clusters_polarity
                ON user_visual_clusters(polarity);
            CREATE TABLE IF NOT EXISTS user_visual_profile_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                embedding_fingerprint TEXT NOT NULL DEFAULT '',
                embedding_dimension INTEGER NOT NULL DEFAULT 0,
                feedback_at TIMESTAMP,
                updated_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT ''
            );
            INSERT OR IGNORE INTO user_visual_profile_state (singleton)
            VALUES (1);
        """)
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(user_visual_clusters)").fetchall()
        }
        for column_name, column_type in {
            "embedding_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "embedding_dimension": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if column_name not in existing_columns:
                self.conn.execute(
                    f"ALTER TABLE user_visual_clusters ADD COLUMN {column_name} {column_type}"
                )

    def get_user_visual_clusters(
        self,
        *,
        embedding_fingerprint: str = "",
        embedding_dimension: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return all stored visual centroids, newest-rebuilt ordering irrelevant.

        Each row: ``{cluster_id, polarity, centroid (list[float]), member_count,
        updated_at}``. ``centroid`` is parsed from JSON; a corrupt row is
        skipped (never feed a bad vector into scoring — pitfall rule 2).
        """
        import json

        params: tuple[Any, ...] = ()
        where = ""
        fingerprint = str(embedding_fingerprint or "").strip()
        if fingerprint:
            where = "WHERE COALESCE(embedding_fingerprint, '') = ?"
            params = (fingerprint,)
            if embedding_dimension is not None and int(embedding_dimension) > 0:
                # Dimension zero is legacy/unknown provenance, not a vector
                # space of its own. Keep it usable until a real observed
                # dimension proves the stored centroid incompatible.
                where += (
                    " AND (COALESCE(embedding_dimension, 0) = 0 "
                    "OR COALESCE(embedding_dimension, 0) = ?)"
                )
                params = (fingerprint, int(embedding_dimension))
        rows = self.conn.execute(
            "SELECT cluster_id, polarity, centroid, member_count, "
            "embedding_fingerprint, embedding_dimension, updated_at "
            f"FROM user_visual_clusters {where}",
            params,
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                vec = json.loads(row["centroid"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(vec, list):
                continue
            out.append(
                {
                    "cluster_id": row["cluster_id"],
                    "polarity": row["polarity"],
                    "centroid": [float(x) for x in vec],
                    "member_count": row["member_count"],
                    "embedding_fingerprint": str(row["embedding_fingerprint"] or ""),
                    "embedding_dimension": int(row["embedding_dimension"] or 0),
                    "updated_at": row["updated_at"],
                }
            )
        return out

    def replace_user_visual_clusters(
        self,
        clusters: list[dict[str, Any]],
        *,
        embedding_fingerprint: str = "",
        embedding_dimension: int = 0,
    ) -> None:
        """Atomically replace all visual centroids.

        ``clusters`` items: ``{polarity, centroid (list[float]), member_count}``.
        Clears the table and re-inserts in one transaction so a rebuild never
        leaves a half-written profile for the hot path to read.
        """
        import json

        cur = self.conn.cursor()
        cur.execute("DELETE FROM user_visual_clusters")
        for c in clusters:
            polarity = str(c.get("polarity", "")).strip()
            if polarity not in {"pos", "neg"}:
                continue
            vec = c.get("centroid")
            if not isinstance(vec, list) or not vec:
                continue
            cur.execute(
                "INSERT INTO user_visual_clusters "
                "(polarity, centroid, member_count, embedding_fingerprint, embedding_dimension) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    polarity,
                    json.dumps([float(x) for x in vec]),
                    int(c.get("member_count", 0) or 0),
                    str(c.get("embedding_fingerprint", embedding_fingerprint) or "").strip(),
                    max(
                        0,
                        int(
                            c.get("embedding_dimension", embedding_dimension) or embedding_dimension
                        ),
                    ),
                ),
            )
        self.conn.commit()

    def get_user_visual_profile_state(self) -> dict[str, Any]:
        """Return the last visual-profile rebuild receipt, if present."""
        row = self.conn.execute(
            "SELECT singleton, embedding_fingerprint, embedding_dimension, feedback_at, "
            "updated_at, status FROM user_visual_profile_state WHERE singleton = 1"
        ).fetchone()
        return dict(row) if row is not None else {}

    def record_user_visual_profile_rebuild(
        self,
        *,
        embedding_fingerprint: str,
        embedding_dimension: int,
        feedback_at: str,
        status: str = "success",
    ) -> None:
        """Persist a successful/confirmed rebuild receipt for throttling."""
        self.conn.execute(
            """
            INSERT INTO user_visual_profile_state (
                singleton, embedding_fingerprint, embedding_dimension,
                feedback_at, updated_at, status
            ) VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                embedding_fingerprint = excluded.embedding_fingerprint,
                embedding_dimension = excluded.embedding_dimension,
                feedback_at = excluded.feedback_at,
                updated_at = excluded.updated_at,
                status = excluded.status
            """,
            (
                str(embedding_fingerprint or "").strip(),
                max(0, int(embedding_dimension)),
                str(feedback_at or "").strip() or None,
                str(status or "success").strip(),
            ),
        )
        self.conn.commit()

    def latest_feedback_at(self) -> str:
        """Newest ``feedback_at`` across recommendations, or '' if none.

        Used to throttle visual-profile rebuilds: only rebuild when feedback
        is newer than the last centroid ``updated_at``.
        """
        row = self.conn.execute(
            "SELECT MAX(feedback_at) AS latest FROM recommendations WHERE feedback_at IS NOT NULL"
        ).fetchone()
        latest = row["latest"] if row is not None else None
        return str(latest) if latest is not None else ""

    def get_feedback_covers(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """All feedback rows with a joinable cover_url, regardless of score.

        ``rebuild_visual_profile`` needs EVERY liked/disliked cover to build
        taste centroids — including feedback on low-relevance items the user
        still took the time to react to. ``get_recommendations`` cannot serve
        this: it applies the pool admission predicate (``confidence >=
        min_score``), which silently drops low-confidence feedback rows, so a
        rebuild via that path saw only a fraction of the feedback and built
        too few / empty centroids. This query bypasses admission and orders
        by feedback time so the most recent reactions win deduplication.
        """
        cursor = self.conn.execute(
            """
            SELECT r.feedback_type, r.bvid,
                   COALESCE(c.cover_url, '') AS cover_url
            FROM recommendations AS r
            LEFT JOIN content_cache AS c
                   ON c.bvid = COALESCE(
                        (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                        (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
                   )
            WHERE r.feedback_type IS NOT NULL
              AND r.feedback_type != ''
              AND COALESCE(c.cover_url, '') != ''
            ORDER BY r.feedback_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _ensure_content_history_tables(self) -> None:
        """Create the bounded audit projection used by the history surfaces.

        Recommendation exposure and click facts already have durable owners in
        ``recommendations`` and ``events``.  Only local saved-list removals lose
        their item snapshot during deletion, so this table records that missing
        fact instead of duplicating all three history streams.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS saved_item_removals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                list_kind       TEXT NOT NULL
                    CHECK (list_kind IN ('favorite', 'watch_later')),
                item_key        TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                content_id      TEXT NOT NULL,
                content_url     TEXT NOT NULL DEFAULT '',
                content_type    TEXT NOT NULL DEFAULT 'video',
                title           TEXT NOT NULL DEFAULT '',
                author_name     TEXT NOT NULL DEFAULT '',
                cover_url       TEXT NOT NULL DEFAULT '',
                removed_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_saved_item_removals_removed
                ON saved_item_removals (removed_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_saved_item_removals_item
                ON saved_item_removals (item_key, removed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_type_created
                ON events (event_type, created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_recommendations_feedback_created
                ON recommendations (feedback_type, feedback_at DESC, id DESC);
        """)
        self.prune_content_history()

    def _ensure_saved_sync_tables(self) -> None:
        """Create normalized saved-content tables and import legacy saved rows once."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS saved_items (
                item_key        TEXT PRIMARY KEY,
                source_platform TEXT NOT NULL,
                content_id      TEXT NOT NULL,
                content_url     TEXT NOT NULL DEFAULT '',
                content_type    TEXT NOT NULL DEFAULT 'video',
                title           TEXT NOT NULL DEFAULT '',
                author_name     TEXT NOT NULL DEFAULT '',
                cover_url       TEXT NOT NULL DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS saved_memberships (
                list_kind TEXT NOT NULL CHECK (list_kind IN ('favorite', 'watch_later')),
                item_key  TEXT NOT NULL REFERENCES saved_items(item_key) ON DELETE CASCADE,
                note      TEXT NOT NULL DEFAULT '',
                added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (list_kind, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_saved_memberships_item_key
                ON saved_memberships(item_key);
            CREATE TABLE IF NOT EXISTS native_save_states (
                list_kind          TEXT NOT NULL,
                item_key           TEXT NOT NULL,
                requested_action   TEXT NOT NULL,
                resolved_action    TEXT NOT NULL DEFAULT '',
                resolved_target    TEXT NOT NULL DEFAULT '',
                status             TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'syncing', 'synced', 'already_synced',
                        'login_required', 'unsupported', 'rate_limited',
                        'extension_required', 'failed'
                    )),
                task_id            TEXT NOT NULL DEFAULT '',
                execution_id       TEXT NOT NULL DEFAULT '',
                task_claimed_at    TIMESTAMP,
                task_started_at    TIMESTAMP,
                task_heartbeat_at  TIMESTAMP,
                task_runner_id     TEXT NOT NULL DEFAULT '',
                last_error_code    TEXT NOT NULL DEFAULT '',
                last_error_message TEXT NOT NULL DEFAULT '',
                last_attempt_at    TIMESTAMP,
                synced_at          TIMESTAMP,
                PRIMARY KEY (list_kind, item_key),
                FOREIGN KEY (list_kind, item_key)
                    REFERENCES saved_memberships(list_kind, item_key) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS native_save_tasks (
                task_id     TEXT PRIMARY KEY,
                list_kind   TEXT NOT NULL CHECK (list_kind IN ('favorite', 'watch_later')),
                trigger     TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS native_save_task_items (
                task_id            TEXT NOT NULL
                    REFERENCES native_save_tasks(task_id) ON DELETE CASCADE,
                item_key           TEXT NOT NULL,
                ordinal            INTEGER NOT NULL,
                requested_action   TEXT NOT NULL,
                resolved_action    TEXT NOT NULL DEFAULT '',
                resolved_target    TEXT NOT NULL DEFAULT '',
                status             TEXT NOT NULL
                    CHECK (status IN (
                        'pending', 'syncing', 'synced', 'already_synced',
                        'login_required', 'unsupported', 'rate_limited',
                        'extension_required', 'failed'
                    )),
                is_live            INTEGER NOT NULL DEFAULT 0 CHECK (is_live IN (0, 1)),
                last_error_code    TEXT NOT NULL DEFAULT '',
                last_error_message TEXT NOT NULL DEFAULT '',
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (task_id, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_native_save_task_items_order
                ON native_save_task_items(task_id, ordinal, item_key);
            CREATE TABLE IF NOT EXISTS extension_native_save_jobs (
                job_id             TEXT PRIMARY KEY,
                platform           TEXT NOT NULL,
                platform_slug      TEXT NOT NULL,
                item_key           TEXT NOT NULL,
                content_id         TEXT NOT NULL,
                content_url        TEXT NOT NULL,
                content_type       TEXT NOT NULL,
                requested_action   TEXT NOT NULL
                    CHECK(requested_action IN ('favorite', 'watch_later')),
                resolved_action    TEXT NOT NULL
                    CHECK(resolved_action IN ('favorite', 'watch_later')),
                target_label       TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN (
                        'pending', 'in_progress', 'synced', 'already_synced',
                        'login_required', 'rate_limited', 'unsupported', 'failed',
                        'extension_required', 'cancelled'
                    )),
                claimed_at         TIMESTAMP,
                completed_at       TIMESTAMP,
                last_error_code    TEXT NOT NULL DEFAULT '',
                last_error_message TEXT NOT NULL DEFAULT '',
                created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_extension_native_save_jobs_claim
                ON extension_native_save_jobs(platform_slug, status, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_extension_native_save_jobs_active_item
                ON extension_native_save_jobs(platform, item_key, requested_action)
                WHERE status IN ('pending', 'in_progress');
            CREATE TABLE IF NOT EXISTS saved_sync_migrations (
                name       TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        native_state_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(native_save_states)").fetchall()
        }
        if "execution_id" not in native_state_columns:
            self.conn.execute(
                "ALTER TABLE native_save_states ADD COLUMN execution_id TEXT NOT NULL DEFAULT ''"
            )
        if "task_runner_id" not in native_state_columns:
            self.conn.execute(
                "ALTER TABLE native_save_states ADD COLUMN task_runner_id TEXT NOT NULL DEFAULT ''"
            )
        for column_name in ("task_claimed_at", "task_started_at", "task_heartbeat_at"):
            if column_name not in native_state_columns:
                self.conn.execute(
                    f"ALTER TABLE native_save_states ADD COLUMN {column_name} TIMESTAMP"
                )
        self.conn.execute(
            """
            UPDATE native_save_states
            SET task_claimed_at = CURRENT_TIMESTAMP
            WHERE status = 'pending' AND task_id != '' AND task_claimed_at IS NULL
            """
        )
        self.conn.execute(
            """
            UPDATE native_save_states
            SET task_heartbeat_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'syncing')
              AND task_id != '' AND task_started_at IS NOT NULL
              AND task_heartbeat_at IS NULL
            """
        )
        self.conn.execute(
            """
            UPDATE native_save_states
            SET task_runner_id = ?
            WHERE status IN ('pending', 'syncing')
              AND task_id != '' AND task_started_at IS NOT NULL
              AND task_runner_id = ''
              AND task_heartbeat_at IS NOT NULL
            """,
            (_LEGACY_NATIVE_SAVE_RUNNER_ID,),
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO native_save_tasks (task_id, list_kind, trigger, created_at)
            SELECT task_id, MIN(list_kind), 'legacy',
                   COALESCE(MIN(task_claimed_at), MIN(last_attempt_at), CURRENT_TIMESTAMP)
            FROM native_save_states
            WHERE task_id != ''
            GROUP BY task_id
            """
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO native_save_task_items (
                task_id, item_key, ordinal, requested_action, resolved_action,
                resolved_target, status, is_live, last_error_code, last_error_message,
                updated_at
            )
            SELECT
                task_id,
                item_key,
                ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY item_key) - 1,
                requested_action,
                resolved_action,
                resolved_target,
                status,
                CASE WHEN status IN ('pending', 'syncing') THEN 1 ELSE 0 END,
                last_error_code,
                last_error_message,
                COALESCE(last_attempt_at, task_claimed_at, CURRENT_TIMESTAMP)
            FROM native_save_states
            WHERE task_id != ''
            """
        )
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            migrated = self.conn.execute(
                "SELECT 1 FROM saved_sync_migrations WHERE name = ?",
                ("legacy_saved_tables_v1",),
            ).fetchone()
            if migrated is None:
                self._migrate_legacy_saved_list("watch_later", "watch_later")
                self._migrate_legacy_saved_list("favorites", "favorite")
                self.conn.execute(
                    "INSERT INTO saved_sync_migrations (name) VALUES (?)",
                    ("legacy_saved_tables_v1",),
                )
            stable_links = self.conn.execute(
                "SELECT 1 FROM saved_sync_migrations WHERE name = ?",
                ("legacy_saved_item_keys_v2",),
            ).fetchone()
            if stable_links is None:
                self._backfill_legacy_saved_item_keys("watch_later", "watch_later")
                self._backfill_legacy_saved_item_keys("favorites", "favorite")
                self.conn.execute(
                    "INSERT INTO saved_sync_migrations (name) VALUES (?)",
                    ("legacy_saved_item_keys_v2",),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self.migrate_legacy_native_save_unsupported()

    def migrate_legacy_native_save_unsupported(self) -> int:
        """Mark only pre-adapter unsupported rows for the six extension sources."""
        migration_name = "extension_adapter_missing_unsupported_v1"
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            migrated = conn.execute(
                "SELECT 1 FROM saved_sync_migrations WHERE name = ?",
                (migration_name,),
            ).fetchone()
            if migrated is not None:
                conn.commit()
                return 0
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET last_error_code = 'unsupported_adapter_missing',
                    last_error_message =
                        'Native save adapter was unavailable; retry is now supported'
                WHERE status = 'unsupported'
                  AND last_error_code IN ('', 'unsupported')
                  AND item_key IN (
                      SELECT item_key
                      FROM saved_items
                      WHERE source_platform IN (
                          'youtube', 'xiaohongshu', 'douyin',
                          'twitter', 'zhihu', 'reddit'
                      )
                  )
                """
            )
            conn.execute(
                "INSERT INTO saved_sync_migrations (name) VALUES (?)",
                (migration_name,),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_or_reuse_extension_native_save_job(
        self, job: ExtensionNativeSaveJob
    ) -> dict[str, Any]:
        """Atomically persist or reuse one active extension native-save job."""
        payload = _validated_extension_native_save_job(job)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT * FROM extension_native_save_jobs
                WHERE platform = ? AND item_key = ? AND requested_action = ?
                  AND status IN ('pending', 'in_progress')
                """,
                (
                    payload["platform"],
                    payload["item_key"],
                    payload["requested_action"],
                ),
            ).fetchone()
            if active is None:
                conn.execute(
                    """
                    INSERT INTO extension_native_save_jobs (
                        job_id, platform, platform_slug, item_key, content_id,
                        content_url, content_type, requested_action, resolved_action,
                        target_label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["job_id"],
                        payload["platform"],
                        payload["platform_slug"],
                        payload["item_key"],
                        payload["content_id"],
                        payload["content_url"],
                        payload["content_type"],
                        payload["requested_action"],
                        payload["resolved_action"],
                        payload["target_label"],
                    ),
                )
                active = conn.execute(
                    "SELECT * FROM extension_native_save_jobs WHERE job_id = ?",
                    (payload["job_id"],),
                ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if active is None:
            raise RuntimeError("extension native-save job insert did not persist")
        return dict(active)

    def claim_extension_native_save_job(
        self, platform_slug: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        """Claim the oldest pending platform job after expiring uncertain stale claims."""
        slug = self._validated_extension_native_save_slug(platform_slug)
        lease = self._validated_extension_native_save_lease(lease_seconds)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._expire_stale_extension_native_save_jobs_in_transaction(conn, slug, lease)
            pending = conn.execute(
                """
                SELECT job_id FROM extension_native_save_jobs
                WHERE platform_slug = ? AND status = 'pending'
                ORDER BY created_at, job_id
                LIMIT 1
                """,
                (slug,),
            ).fetchone()
            claimed = None
            if pending is not None:
                job_id = str(pending["job_id"])
                cursor = conn.execute(
                    """
                    UPDATE extension_native_save_jobs
                    SET status = 'in_progress',
                        claimed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                        updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                    WHERE job_id = ? AND status = 'pending'
                    """,
                    (job_id,),
                )
                if cursor.rowcount == 1:
                    claimed = conn.execute(
                        "SELECT * FROM extension_native_save_jobs WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return dict(claimed) if claimed is not None else None

    def complete_extension_native_save_job(
        self,
        job_id: str,
        platform_slug: str,
        item_key: str,
        status: str,
        error_code: str,
        error_message: str,
    ) -> bool:
        """Complete one claimed job when slug, UUID, and item identity match."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        safe_slug = self._validated_extension_native_save_slug(platform_slug)
        safe_item_key = _validated_extension_native_save_text(item_key, "item_key", max_length=768)
        safe_status, safe_code, safe_message = _validated_extension_native_save_result(
            status, error_code, error_message
        )
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE extension_native_save_jobs
                SET status = ?, last_error_code = ?, last_error_message = ?,
                    completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                    updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                WHERE job_id = ? AND platform_slug = ? AND item_key = ?
                  AND status = 'in_progress'
                """,
                (safe_status, safe_code, safe_message, safe_job_id, safe_slug, safe_item_key),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def cancel_unclaimed_extension_native_save_job(self, job_id: str) -> bool:
        """Cancel a pending job without touching a possibly state-changing claimed job."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE extension_native_save_jobs
                SET status = 'cancelled',
                    completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                    updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                WHERE job_id = ? AND status = 'pending'
                """,
                (safe_job_id,),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def mark_unclaimed_extension_native_save_job_extension_required(self, job_id: str) -> bool:
        """Durably mark a still-pending job when no extension claims it in time."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE extension_native_save_jobs
                SET status = 'extension_required',
                    last_error_code = 'extension_unavailable',
                    last_error_message = 'OpenBiliClaw extension is unavailable',
                    completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                    updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
                WHERE job_id = ? AND status = 'pending'
                """,
                (safe_job_id,),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def get_extension_native_save_job(self, job_id: str) -> dict[str, Any] | None:
        """Return a copied durable extension job row by canonical UUID."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        try:
            row = conn.execute(
                "SELECT * FROM extension_native_save_jobs WHERE job_id = ?", (safe_job_id,)
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def owns_extension_native_save_job(self, job_id: str, platform_slug: str | None = None) -> bool:
        """Return global job ownership, optionally restricted to one exact slug."""
        safe_job_id = _validated_extension_native_save_uuid(job_id, "job_id")
        conn = self.open_connection()
        if platform_slug is None:
            try:
                row = conn.execute(
                    "SELECT 1 FROM extension_native_save_jobs WHERE job_id = ?",
                    (safe_job_id,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()
        try:
            safe_slug = self._validated_extension_native_save_slug(platform_slug)
            row = conn.execute(
                "SELECT 1 FROM extension_native_save_jobs WHERE job_id = ? AND platform_slug = ?",
                (safe_job_id, safe_slug),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def expire_stale_extension_native_save_jobs(
        self, platform_slug: str, lease_seconds: float
    ) -> int:
        """Fail stale claimed writes without returning them to the pending queue."""
        slug = self._validated_extension_native_save_slug(platform_slug)
        lease = self._validated_extension_native_save_lease(lease_seconds)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            count = self._expire_stale_extension_native_save_jobs_in_transaction(conn, slug, lease)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return count

    def _expire_stale_extension_native_save_jobs_in_transaction(
        self, conn: sqlite3.Connection, platform_slug: str, lease_seconds: float
    ) -> int:
        cursor = conn.execute(
            """
            UPDATE extension_native_save_jobs
            SET status = 'failed',
                last_error_code = 'extension_task_timeout',
                last_error_message = 'Extension native-save task timed out',
                completed_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now'),
                updated_at = STRFTIME('%Y-%m-%d %H:%M:%f', 'now')
            WHERE platform_slug = ? AND status = 'in_progress'
              AND claimed_at IS NOT NULL
              AND (JULIANDAY('now') - JULIANDAY(claimed_at)) * 86400.0 >= ?
            """,
            (platform_slug, lease_seconds),
        )
        return cursor.rowcount

    @staticmethod
    def _validated_extension_native_save_slug(platform_slug: object) -> str:
        slug = _validated_extension_native_save_text(
            platform_slug, "platform_slug", max_length=16
        ).lower()
        if slug not in _EXTENSION_NATIVE_SAVE_SLUGS:
            raise ValueError("platform_slug is invalid")
        return slug

    @staticmethod
    def _validated_extension_native_save_lease(lease_seconds: object) -> float:
        if isinstance(lease_seconds, bool):
            raise ValueError("lease_seconds must be a positive finite number")
        try:
            lease = float(cast("Any", lease_seconds))
        except (TypeError, ValueError) as exc:
            raise ValueError("lease_seconds must be a positive finite number") from exc
        if not math.isfinite(lease) or lease <= 0:
            raise ValueError("lease_seconds must be a positive finite number")
        return lease

    def _migrate_legacy_saved_list(self, table_name: str, list_kind: SavedListKind) -> None:
        """Copy one trusted legacy saved table into normalized storage."""
        if table_name not in {"watch_later", "favorites"}:
            raise ValueError(f"unsupported legacy saved table: {table_name}")
        platform_sql, content_id_sql, item_key_sql = self._legacy_saved_identity_sql()
        cache_join_sql = """
            c.bvid = COALESCE(
                (SELECT exact.bvid FROM content_cache AS exact WHERE exact.bvid = legacy.bvid),
                (
                    SELECT linked.bvid
                    FROM content_cache AS linked
                    WHERE linked.item_key = NULLIF(TRIM(legacy.item_key), '')
                )
            )
        """

        self.conn.execute(
            f"""
            INSERT OR IGNORE INTO saved_items (
                item_key, source_platform, content_id, content_url, content_type,
                title, author_name, cover_url, created_at, updated_at
            )
            SELECT
                {item_key_sql},
                {platform_sql},
                {content_id_sql},
                COALESCE(c.content_url, ''),
                COALESCE(NULLIF(c.content_type, ''), 'video'),
                COALESCE(c.title, ''),
                COALESCE(NULLIF(c.author_name, ''), c.up_name, ''),
                COALESCE(c.cover_url, ''),
                legacy.added_at,
                legacy.added_at
            FROM {table_name} AS legacy
            LEFT JOIN content_cache AS c ON {cache_join_sql}
            """
        )
        self.conn.execute(
            f"""
            INSERT OR IGNORE INTO saved_memberships (list_kind, item_key, note, added_at)
            SELECT ?, {item_key_sql}, COALESCE(legacy.note, ''), legacy.added_at
            FROM {table_name} AS legacy
            LEFT JOIN content_cache AS c ON {cache_join_sql}
            """,
            (list_kind,),
        )
        self._backfill_legacy_saved_item_keys(table_name, list_kind)

    @staticmethod
    def _legacy_saved_identity_sql() -> tuple[str, str, str]:
        """Return shared SQL expressions for canonicalizing one joined legacy saved row."""
        complete_identity_sql = """
            NULLIF(TRIM(c.source_platform), '') IS NOT NULL
            AND NULLIF(TRIM(c.content_id), '') IS NOT NULL
        """
        platform_sql = f"""
            CASE WHEN {complete_identity_sql} THEN
                CASE LOWER(TRIM(c.source_platform))
                    WHEN 'bili' THEN 'bilibili'
                    WHEN 'xhs' THEN 'xiaohongshu'
                    WHEN 'dy' THEN 'douyin'
                    WHEN 'yt' THEN 'youtube'
                    WHEN 'x' THEN 'twitter'
                    WHEN 'zh' THEN 'zhihu'
                    WHEN 'rd' THEN 'reddit'
                    ELSE LOWER(TRIM(c.source_platform))
                END
            ELSE 'bilibili'
            END
        """
        content_id_sql = (
            f"CASE WHEN {complete_identity_sql} THEN TRIM(c.content_id) ELSE legacy.bvid END"
        )
        item_key_sql = f"({platform_sql}) || ':' || ({content_id_sql})"
        return platform_sql, content_id_sql, item_key_sql

    def _backfill_legacy_saved_item_keys(
        self,
        table_name: str,
        list_kind: SavedListKind,
    ) -> None:
        """Persist the migration-time identity link without creating memberships."""
        if table_name not in {"watch_later", "favorites"}:
            raise ValueError(f"unsupported legacy saved table: {table_name}")
        _, _, item_key_sql = self._legacy_saved_identity_sql()
        self.conn.execute(
            f"""
            UPDATE {table_name} AS legacy
            SET item_key = COALESCE(
                (
                    SELECT {item_key_sql}
                    FROM content_cache AS c
                    JOIN saved_memberships AS m
                      ON m.list_kind = ? AND m.item_key = ({item_key_sql})
                    WHERE c.bvid = legacy.bvid
                    LIMIT 1
                ),
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM saved_memberships AS m
                    WHERE m.list_kind = ?
                      AND m.item_key = 'bilibili:' || legacy.bvid
                ) THEN 'bilibili:' || legacy.bvid ELSE '' END
            )
            WHERE item_key = ''
            """,
            (list_kind, list_kind),
        )

    @staticmethod
    def _saved_list_kind(value: str) -> SavedListKind:
        list_kind = value.strip()
        if list_kind not in {"favorite", "watch_later"}:
            raise ValueError("list_kind must be 'favorite' or 'watch_later'")
        return cast("SavedListKind", list_kind)

    @staticmethod
    def _native_task_id(value: str) -> str:
        task_id = value.strip()
        if not task_id:
            raise ValueError("task_id must not be blank")
        return task_id

    @staticmethod
    def _native_execution_id(value: str) -> str:
        execution_id = value.strip()
        if not execution_id:
            raise ValueError("execution_id must not be blank")
        return execution_id

    @staticmethod
    def _native_runner_id(value: str) -> str:
        runner_id = value.strip()
        if not runner_id:
            raise ValueError("runner_id must not be blank")
        if runner_id.startswith(_NATIVE_INTERNAL_RUNNER_PREFIX):
            raise ValueError("runner_id uses a reserved internal prefix")
        return runner_id

    def _bilibili_saved_item_input(self, bvid: str) -> SavedItemInput:
        """Build a Bilibili compatibility input with any cached metadata snapshot."""
        content_id = bvid.strip()
        row = self.conn.execute(
            """
            SELECT title, up_name, author_name, cover_url, content_url, content_type
            FROM content_cache
            WHERE bvid = ?
            """,
            (content_id,),
        ).fetchone()
        if row is None:
            return SavedItemInput(source_platform="bilibili", content_id=content_id)
        return SavedItemInput(
            source_platform="bilibili",
            content_id=content_id,
            content_url=str(row["content_url"] or ""),
            content_type=str(row["content_type"] or "video"),
            title=str(row["title"] or ""),
            author_name=str(row["author_name"] or row["up_name"] or ""),
            cover_url=str(row["cover_url"] or ""),
        )

    def _resolve_legacy_saved_item_key(self, list_kind: str, content_id: str) -> str | None:
        """Resolve a legacy raw-ID removal to one unambiguous normalized membership."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_content_id = content_id.strip()
        bilibili_key = f"bilibili:{normalized_content_id}"
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT m.item_key
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            WHERE m.list_kind = ?
              AND (m.item_key = ? OR i.content_id = ?)
            ORDER BY CASE WHEN m.item_key = ? THEN 0 ELSE 1 END, m.item_key
            """,
            (normalized_kind, bilibili_key, normalized_content_id, bilibili_key),
        ).fetchall()
        if rows and str(rows[0]["item_key"]) == bilibili_key:
            return bilibili_key
        if len(rows) == 1:
            return str(rows[0]["item_key"])
        if len(rows) > 1:
            return None
        return bilibili_key

    @staticmethod
    def _saved_membership_select() -> str:
        return """
            SELECT
                m.list_kind,
                i.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                COALESCE(NULLIF(i.title, ''), (
                    SELECT cc.title FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.title) AS title,
                COALESCE(NULLIF(i.author_name, ''), (
                    SELECT COALESCE(NULLIF(cc.up_name, ''), cc.author_name)
                    FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.author_name) AS author_name,
                COALESCE(NULLIF(i.cover_url, ''), (
                    SELECT cc.cover_url FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), '') AS cover_url,
                i.created_at,
                i.updated_at,
                m.note,
                m.added_at,
                COALESCE(n.requested_action, '') AS requested_action,
                COALESCE(n.resolved_action, '') AS resolved_action,
                COALESCE(n.resolved_target, '') AS resolved_target,
                COALESCE(n.status, 'pending') AS sync_status,
                COALESCE(n.task_id, '') AS sync_task_id,
                COALESCE(n.last_error_code, '') AS last_error_code,
                COALESCE(n.last_error_message, '') AS last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            LEFT JOIN native_save_states AS n
                ON n.list_kind = m.list_kind AND n.item_key = m.item_key
        """

    def upsert_saved_membership(
        self,
        list_kind: str,
        item: SavedItemInput,
        note: str = "",
    ) -> dict[str, Any]:
        """Atomically upsert an item snapshot and its local list membership."""
        normalized_kind = self._saved_list_kind(list_kind)
        item_key = item.item_key
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO saved_items (
                    item_key, source_platform, content_id, content_url, content_type,
                    title, author_name, cover_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    source_platform = excluded.source_platform,
                    content_id = excluded.content_id,
                    content_url = excluded.content_url,
                    content_type = excluded.content_type,
                    title = excluded.title,
                    author_name = excluded.author_name,
                    cover_url = excluded.cover_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_key,
                    item.platform,
                    item.content_id.strip(),
                    item.content_url.strip(),
                    item.content_type.strip() or "video",
                    item.title.strip(),
                    item.author_name.strip(),
                    item.cover_url.strip(),
                ),
            )
            conn.execute(
                """
                INSERT INTO saved_memberships (list_kind, item_key, note)
                VALUES (?, ?, ?)
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    note = excluded.note,
                    added_at = CURRENT_TIMESTAMP
                """,
                (normalized_kind, item_key, note),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        row = self.get_saved_membership(normalized_kind, item_key)
        if row is None:  # pragma: no cover - transaction succeeded but row vanished externally
            raise RuntimeError("saved membership disappeared after upsert")
        return row

    def remove_saved_membership(self, list_kind: str, item_key: str) -> bool:
        """Remove a normalized membership and any matching legacy compatibility row."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        legacy_table = "favorites" if normalized_kind == "favorite" else "watch_later"
        legacy_bvid = (
            normalized_key.removeprefix("bilibili:")
            if normalized_key.startswith("bilibili:")
            else None
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            removed_snapshot = conn.execute(
                self._saved_membership_select() + " WHERE m.list_kind = ? AND m.item_key = ?",
                (normalized_kind, normalized_key),
            ).fetchone()
            active_state = conn.execute(
                """
                SELECT task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                  AND status IN ('pending', 'syncing') AND task_id != ''
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if active_state is not None:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'not_saved_locally',
                        last_error_message = 'Item is not saved locally',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status IN ('pending', 'syncing')
                    """,
                    (str(active_state["task_id"]), normalized_key),
                )
            cursor = conn.execute(
                "DELETE FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            )
            removed = int(cursor.rowcount or 0) > 0
            if removed and removed_snapshot is not None:
                conn.execute(
                    """
                    INSERT INTO saved_item_removals (
                        list_kind, item_key, source_platform, content_id,
                        content_url, content_type, title, author_name, cover_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_kind,
                        normalized_key,
                        str(removed_snapshot["source_platform"] or ""),
                        str(removed_snapshot["content_id"] or ""),
                        str(removed_snapshot["content_url"] or ""),
                        str(removed_snapshot["content_type"] or "video"),
                        str(removed_snapshot["title"] or ""),
                        str(removed_snapshot["author_name"] or ""),
                        str(removed_snapshot["cover_url"] or ""),
                    ),
                )
            conn.execute(
                """
                DELETE FROM saved_item_removals
                WHERE removed_at < datetime('now', ?)
                """,
                (f"-{CONTENT_HISTORY_RETENTION_DAYS} days",),
            )
            direct_bilibili_clause = "bvid = ? OR" if legacy_bvid is not None else ""
            legacy_params = (legacy_bvid, normalized_key) if legacy_bvid else (normalized_key,)
            legacy_cursor = conn.execute(
                f"""
                DELETE FROM {legacy_table}
                WHERE {direct_bilibili_clause} item_key = ?
                """,
                legacy_params,
            )
            removed = removed or int(legacy_cursor.rowcount or 0) > 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        return removed

    def get_saved_membership(self, list_kind: str, item_key: str) -> dict[str, Any] | None:
        """Return one normalized membership with its current native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            self._saved_membership_select() + " WHERE m.list_kind = ? AND m.item_key = ?",
            (normalized_kind, item_key.strip()),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_saved_memberships(
        self,
        list_kind: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List normalized memberships newest first with native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            self._saved_membership_select()
            + " WHERE m.list_kind = ? ORDER BY m.added_at DESC, m.item_key ASC LIMIT ? OFFSET ?",
            (normalized_kind, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
        resolved_action: str = "",
        resolved_target: str = "",
        status: str = "pending",
        task_id: str = "",
        execution_id: str = "",
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> None:
        """Persist the latest native-save routing and execution state for one item."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        normalized_task_id = task_id.strip()
        if not isinstance(status, str) or status not in NATIVE_SAVE_STATUSES:
            raise ValueError("invalid native save status")
        if task_id and not normalized_task_id:
            raise ValueError("task_id must not be blank")
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            membership = conn.execute(
                "SELECT 1 FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            ).fetchone()
            if membership is None:
                raise ValueError(
                    f"saved membership does not exist: {normalized_kind}/{normalized_key}"
                )
            if execution_id or status == "syncing" or (status == "pending" and normalized_task_id):
                raise ValueError("active task ownership must use the atomic claim APIs")
            current = conn.execute(
                """
                SELECT status, task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if (
                current is not None
                and str(current["status"]) in {"pending", "syncing"}
                and str(current["task_id"])
            ):
                raise ValueError("active task ownership must use the atomic claim APIs")
            if current is not None and status == "pending" and str(current["status"]) != "pending":
                raise ValueError("invalid native save status transition to pending")
            conn.execute(
                """
                INSERT INTO native_save_states (
                    list_kind, item_key, requested_action, resolved_action, resolved_target,
                    status, task_id, execution_id, last_error_code, last_error_message,
                    last_attempt_at, synced_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CASE WHEN ? = 'pending' THEN NULL ELSE CURRENT_TIMESTAMP END,
                    CASE WHEN ? IN ('synced', 'already_synced')
                        THEN CURRENT_TIMESTAMP ELSE NULL END
                )
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    requested_action = excluded.requested_action,
                    resolved_action = excluded.resolved_action,
                    resolved_target = excluded.resolved_target,
                    status = excluded.status,
                    task_id = excluded.task_id,
                    execution_id = excluded.execution_id,
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    last_attempt_at = CASE
                        WHEN excluded.status = 'pending' THEN native_save_states.last_attempt_at
                        ELSE CURRENT_TIMESTAMP
                    END,
                    synced_at = CASE
                        WHEN excluded.status IN ('synced', 'already_synced')
                            THEN CURRENT_TIMESTAMP
                        ELSE native_save_states.synced_at
                    END
                """,
                (
                    normalized_kind,
                    normalized_key,
                    requested_action,
                    resolved_action,
                    resolved_target,
                    status,
                    normalized_task_id,
                    "",
                    last_error_code,
                    last_error_message,
                    status,
                    status,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
    ) -> dict[str, Any]:
        """Insert a pending state only when absent and return the effective state.

        Existing active, claimed, syncing, retryable, and terminal rows are never
        modified. This closes the local-save/task-claim read-then-write race.
        """
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            membership = conn.execute(
                "SELECT 1 FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            ).fetchone()
            if membership is None:
                raise ValueError(
                    f"saved membership does not exist: {normalized_kind}/{normalized_key}"
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO native_save_states (
                    list_kind, item_key, requested_action, status
                ) VALUES (?, ?, ?, 'pending')
                """,
                (normalized_kind, normalized_key, requested_action),
            )
            row = conn.execute(
                """
                SELECT requested_action, resolved_action, resolved_target, status,
                       task_id, execution_id, last_error_code, last_error_message,
                       last_attempt_at, synced_at, task_claimed_at, task_started_at,
                       task_heartbeat_at, task_runner_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if row is None:  # pragma: no cover - insert/select share one write transaction
            raise RuntimeError("native save state disappeared during ensure")
        return dict(row)

    def claim_native_sync_task(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
        task_id: str,
    ) -> list[str]:
        """Atomically assign eligible memberships to one durable pending task.

        A pending row with a non-empty task owner is deliberately ineligible so
        duplicate/manual task creation cannot steal it and invalidate polling for
        the original task ID.
        """
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if raw_keys and not cleaned_keys:
            raise ValueError("item_keys must contain at least one non-blank key")
        params: list[Any] = [normalized_kind]
        item_filter = ""
        if cleaned_keys:
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND m.item_key IN ({placeholders})"
            params.extend(cleaned_keys)

        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT m.item_key
                FROM saved_memberships AS m
                LEFT JOIN native_save_states AS n
                  ON n.list_kind = m.list_kind AND n.item_key = m.item_key
                WHERE m.list_kind = ?
                  AND (
                      n.status IS NULL
                      OR (n.status = 'pending' AND n.task_id = '')
                      OR n.status IN (
                          'login_required', 'rate_limited',
                          'extension_required', 'failed'
                      )
                  )
                """
                + item_filter
                + " ORDER BY m.added_at DESC, m.item_key ASC",
                params,
            ).fetchall()
            claimed_keys = [str(row["item_key"]) for row in rows]
            for item_key in claimed_keys:
                conn.execute(
                    """
                    INSERT INTO native_save_states (
                        list_kind, item_key, requested_action, status, task_id,
                        execution_id, resolved_action, resolved_target,
                        last_error_code, last_error_message, last_attempt_at,
                        task_claimed_at, task_started_at
                    )
                    VALUES (?, ?, ?, 'pending', ?, '', '', '', '', '', NULL,
                            CURRENT_TIMESTAMP, NULL)
                    ON CONFLICT(list_kind, item_key) DO UPDATE SET
                        requested_action = excluded.requested_action,
                        status = 'pending',
                        task_id = excluded.task_id,
                        execution_id = '',
                        resolved_action = '',
                        resolved_target = '',
                        last_error_code = '',
                        last_error_message = '',
                        task_claimed_at = CURRENT_TIMESTAMP,
                        task_started_at = NULL,
                        task_heartbeat_at = NULL,
                        task_runner_id = ''
                    """,
                    (normalized_kind, item_key, normalized_kind, normalized_task_id),
                )
            conn.commit()
            return claimed_keys
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_native_sync_task_snapshot(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
        task_id: str,
        trigger: str,
    ) -> list[dict[str, Any]]:
        """Create one durable task ledger and atomically claim its live items.

        ``item_keys is None`` selects every currently eligible membership. An
        explicit selection snapshots every requested key in caller order:
        missing keys become terminal ``not_saved_locally`` failures, terminal
        native states become terminal no-ops, and rows already owned by another
        task become terminal ``sync_already_in_progress`` no-ops.
        """
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_trigger = trigger.strip()
        if not normalized_trigger:
            raise ValueError("trigger must not be blank")
        explicit_selection = item_keys is not None
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if (
            raw_keys
            and len(cleaned_keys) != len(dict.fromkeys(raw_keys))
            and any(not key.strip() for key in raw_keys)
        ):
            raise ValueError("item_keys must not contain blank keys")

        retryable_statuses = {
            "login_required",
            "rate_limited",
            "extension_required",
            "failed",
        }
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO native_save_tasks (task_id, list_kind, trigger)
                VALUES (?, ?, ?)
                """,
                (normalized_task_id, normalized_kind, normalized_trigger),
            )
            if explicit_selection:
                selected_keys = cleaned_keys
            else:
                rows = conn.execute(
                    """
                    SELECT m.item_key
                    FROM saved_memberships AS m
                    LEFT JOIN native_save_states AS n
                      ON n.list_kind = m.list_kind AND n.item_key = m.item_key
                    WHERE m.list_kind = ?
                      AND (
                          n.status IS NULL
                          OR (n.status = 'pending' AND n.task_id = '')
                          OR n.status IN (
                              'login_required', 'rate_limited',
                              'extension_required', 'failed'
                          )
                          OR (
                              n.status = 'unsupported'
                              AND n.last_error_code = 'unsupported_adapter_missing'
                          )
                      )
                    ORDER BY m.added_at DESC, m.item_key ASC
                    """,
                    (normalized_kind,),
                ).fetchall()
                selected_keys = [str(row["item_key"]) for row in rows]

            for ordinal, item_key in enumerate(selected_keys):
                row = conn.execute(
                    """
                    SELECT
                        m.item_key,
                        n.requested_action,
                        n.resolved_action,
                        n.resolved_target,
                        n.status,
                        n.task_id,
                        n.last_error_code,
                        n.last_error_message
                    FROM saved_memberships AS m
                    LEFT JOIN native_save_states AS n
                      ON n.list_kind = m.list_kind AND n.item_key = m.item_key
                    WHERE m.list_kind = ? AND m.item_key = ?
                    """,
                    (normalized_kind, item_key),
                ).fetchone()
                requested_action: str = normalized_kind
                resolved_action: str = normalized_kind
                resolved_target = ""
                status: str = "failed"
                is_live = 0
                error_code = "not_saved_locally"
                error_message = "Item is not saved locally"

                if row is not None:
                    current_status = str(row["status"] or "pending")
                    current_task_id = str(row["task_id"] or "")
                    requested_action = str(row["requested_action"] or normalized_kind)
                    resolved_action = str(row["resolved_action"] or normalized_kind)
                    resolved_target = str(row["resolved_target"] or "")
                    error_code = str(row["last_error_code"] or "")
                    error_message = str(row["last_error_message"] or "")
                    eligible = (
                        row["status"] is None
                        or (current_status == "pending" and not current_task_id)
                        or current_status in retryable_statuses
                        or (
                            current_status == "unsupported"
                            and error_code == "unsupported_adapter_missing"
                        )
                    )
                    if eligible:
                        status = "pending"
                        is_live = 1
                        resolved_action = normalized_kind
                        resolved_target = ""
                        error_code = ""
                        error_message = ""
                        conn.execute(
                            """
                            INSERT INTO native_save_states (
                                list_kind, item_key, requested_action, status, task_id,
                                execution_id, resolved_action, resolved_target,
                                last_error_code, last_error_message, last_attempt_at,
                                task_claimed_at, task_started_at
                            )
                            VALUES (?, ?, ?, 'pending', ?, '', '', '', '', '', NULL,
                                    CURRENT_TIMESTAMP, NULL)
                            ON CONFLICT(list_kind, item_key) DO UPDATE SET
                                requested_action = excluded.requested_action,
                                status = 'pending',
                                task_id = excluded.task_id,
                                execution_id = '',
                                resolved_action = '',
                                resolved_target = '',
                                last_error_code = '',
                                last_error_message = '',
                                task_claimed_at = CURRENT_TIMESTAMP,
                                task_started_at = NULL,
                                task_heartbeat_at = NULL,
                                task_runner_id = ''
                            """,
                            (
                                normalized_kind,
                                item_key,
                                normalized_kind,
                                normalized_task_id,
                            ),
                        )
                    elif current_status in NATIVE_SAVE_TERMINAL_STATUSES:
                        status = current_status
                    else:
                        status = "failed"
                        error_code = "sync_already_in_progress"
                        error_message = "Item already belongs to an active sync task"

                conn.execute(
                    """
                    INSERT INTO native_save_task_items (
                        task_id, item_key, ordinal, requested_action, resolved_action,
                        resolved_target, status, is_live, last_error_code,
                        last_error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_task_id,
                        item_key,
                        ordinal,
                        requested_action,
                        resolved_action,
                        resolved_target,
                        status,
                        is_live,
                        error_code,
                        error_message,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.list_native_sync_task_items(normalized_task_id)

    def native_sync_task_exists(self, task_id: str) -> bool:
        """Return whether a durable task ledger exists, including empty tasks."""
        normalized_task_id = self._native_task_id(task_id)
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT 1 FROM native_save_tasks WHERE task_id = ?",
            (normalized_task_id,),
        ).fetchone()
        return row is not None

    def list_native_sync_task_items(self, task_id: str) -> list[dict[str, Any]]:
        """Return immutable task membership with its task-scoped result snapshot."""
        normalized_task_id = self._native_task_id(task_id)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT
                t.list_kind,
                i.task_id,
                i.item_key,
                i.ordinal,
                i.requested_action,
                i.resolved_action,
                i.resolved_target,
                i.status,
                i.is_live,
                i.last_error_code,
                i.last_error_message,
                i.updated_at
            FROM native_save_task_items AS i
            JOIN native_save_tasks AS t ON t.task_id = i.task_id
            WHERE i.task_id = ?
            ORDER BY i.ordinal ASC, i.item_key ASC
            """,
            (normalized_task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def discard_native_sync_task(self, task_id: str) -> bool:
        """Delete an unreturned task ledger after task-starter registration fails."""
        normalized_task_id = self._native_task_id(task_id)
        cursor = self._execute_write(
            "DELETE FROM native_save_tasks WHERE task_id = ?",
            (normalized_task_id,),
        )
        return int(cursor.rowcount or 0) > 0

    def release_native_sync_task(self, task_id: str) -> int:
        """Release pending ownership when a task could not be registered."""
        normalized_task_id = self._native_task_id(task_id)
        cursor = self._execute_write(
            """
            UPDATE native_save_states
            SET task_id = '', execution_id = '', task_claimed_at = NULL,
                task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
            WHERE task_id = ? AND status = 'pending' AND execution_id = ''
            """,
            (normalized_task_id,),
        )
        return int(cursor.rowcount or 0)

    def release_stale_pending_native_sync_tasks(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None = None,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Release pending owners whose task never started or lost its heartbeat."""
        normalized_kind = self._saved_list_kind(list_kind)
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if raw_keys and not cleaned_keys:
            raise ValueError("item_keys must contain at least one non-blank key")
        item_filter = ""
        params: list[Any] = [normalized_kind]
        if cleaned_keys:
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND item_key IN ({placeholders})"
            params.extend(cleaned_keys)
        age = max(0, int(stale_after_seconds))
        cutoff = f"-{age} seconds"
        params.extend((cutoff, cutoff))
        where_sql = (
            """
            WHERE list_kind = ? AND status = 'pending' AND task_id != ''
            """
            + item_filter
            + """
              AND (
                  (task_started_at IS NULL AND task_claimed_at IS NOT NULL
                   AND task_claimed_at <= datetime('now', ?))
                  OR
                  (task_started_at IS NOT NULL
                   AND (task_heartbeat_at IS NULL
                        OR task_heartbeat_at <= datetime('now', ?)))
              )
            """
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT task_id, item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (str(row["task_id"]), str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET task_id = '', execution_id = '', task_claimed_at = NULL,
                    task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_stale_pending_native_sync_task(
        self,
        task_id: str,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Release stale pending rows while polling one known task."""
        normalized_task_id = self._native_task_id(task_id)
        age = max(0, int(stale_after_seconds))
        cutoff = f"-{age} seconds"
        params = (normalized_task_id, cutoff, cutoff)
        where_sql = """
            WHERE task_id = ? AND status = 'pending'
              AND (
                  (task_started_at IS NULL AND task_claimed_at IS NOT NULL
                   AND task_claimed_at <= datetime('now', ?))
                  OR
                  (task_started_at IS NOT NULL
                   AND (task_heartbeat_at IS NULL
                        OR task_heartbeat_at <= datetime('now', ?)))
              )
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (normalized_task_id, str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET task_id = '', execution_id = '', task_claimed_at = NULL,
                    task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_native_sync_task_runner(
        self,
        task_id: str,
        runner_id: str,
        *,
        stale_after_seconds: int = 300,
    ) -> bool:
        """Atomically acquire the single batch-runner lease for a task."""
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        age = max(0, int(stale_after_seconds))
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT 1
                FROM native_save_states
                WHERE task_id = ? AND status IN ('pending', 'syncing')
                LIMIT 1
                """,
                (normalized_task_id,),
            ).fetchone()
            if active is None:
                conn.commit()
                return True
            conflicting = conn.execute(
                """
                SELECT 1
                FROM native_save_states
                WHERE task_id = ? AND status IN ('pending', 'syncing')
                  AND task_runner_id NOT IN ('', ?)
                  AND task_heartbeat_at IS NOT NULL
                  AND task_heartbeat_at > datetime('now', ?)
                LIMIT 1
                """,
                (normalized_task_id, normalized_runner_id, f"-{age} seconds"),
            ).fetchone()
            if conflicting is not None:
                conn.commit()
                return False
            conn.execute(
                """
                UPDATE native_save_states
                SET task_runner_id = ?,
                    task_started_at = COALESCE(task_started_at, CURRENT_TIMESTAMP),
                    task_heartbeat_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND status IN ('pending', 'syncing')
                """,
                (normalized_runner_id, normalized_task_id),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat_native_sync_task(self, task_id: str, runner_id: str) -> int:
        """Refresh the task lease protecting all remaining batch rows."""
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET task_heartbeat_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND task_runner_id = ? AND task_started_at IS NOT NULL
                  AND status IN ('pending', 'syncing')
                """,
                (normalized_task_id, normalized_runner_id),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_pending_native_sync_task(self, task_id: str, runner_id: str) -> int:
        """Release unclaimed pending rows when a runner exits normally or by cancellation."""
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        params = (normalized_task_id, normalized_runner_id)
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT item_key
                FROM native_save_states
                WHERE task_id = ? AND task_runner_id = ?
                  AND status = 'pending' AND execution_id = ''
                """,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (normalized_task_id, str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET task_id = '', execution_id = '', task_claimed_at = NULL,
                    task_started_at = NULL, task_heartbeat_at = NULL, task_runner_id = ''
                WHERE task_id = ? AND task_runner_id = ?
                  AND status = 'pending' AND execution_id = ''
                """,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_native_save_item(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        runner_id: str,
        execution_id: str,
    ) -> bool:
        """Atomically claim one pending task item for adapter execution."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_runner_id = self._native_runner_id(runner_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET status = 'syncing', execution_id = ?, last_attempt_at = CURRENT_TIMESTAMP
                WHERE list_kind = ? AND item_key = ? AND task_id = ? AND task_runner_id = ?
                  AND status = 'pending' AND execution_id = ''
                """,
                (
                    normalized_execution_id,
                    normalized_kind,
                    normalized_key,
                    normalized_task_id,
                    normalized_runner_id,
                ),
            )
            claimed = int(cursor.rowcount or 0) > 0
            if claimed:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'syncing', updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status = 'pending'
                    """,
                    (normalized_task_id, normalized_key),
                )
            conn.commit()
            return claimed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_native_save_claim_route(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
        resolved_action: str,
        resolved_target: str,
    ) -> bool:
        """Persist the router-owned destination for a live execution claim."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET resolved_action = ?, resolved_target = ?
                WHERE list_kind = ? AND item_key = ? AND task_id = ?
                  AND status = 'syncing' AND execution_id = ?
                """,
                (
                    resolved_action,
                    resolved_target,
                    normalized_kind,
                    normalized_key,
                    normalized_task_id,
                    normalized_execution_id,
                ),
            )
            updated = int(cursor.rowcount or 0) > 0
            if updated:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET resolved_action = ?, resolved_target = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status = 'syncing'
                    """,
                    (
                        resolved_action,
                        resolved_target,
                        normalized_task_id,
                        normalized_key,
                    ),
                )
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat_native_save_claim(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
    ) -> bool:
        """Refresh a live adapter lease only while the execution owner matches."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        conn = self.open_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET last_attempt_at = CURRENT_TIMESTAMP
                WHERE list_kind = ? AND item_key = ? AND task_id = ?
                  AND status = 'syncing' AND execution_id = ?
                """,
                (
                    normalized_kind,
                    item_key.strip(),
                    normalized_task_id,
                    normalized_execution_id,
                ),
            )
            conn.commit()
            return int(cursor.rowcount or 0) > 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_native_save_claim(
        self,
        list_kind: str,
        item_key: str,
        task_id: str,
        execution_id: str,
        *,
        requested_action: str,
        resolved_action: str,
        resolved_target: str,
        status: str,
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> bool:
        """Complete one item only when the caller still owns its execution claim."""
        if not isinstance(status, str) or status not in NATIVE_SAVE_TERMINAL_STATUSES:
            raise ValueError("completion requires a terminal status")
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_task_id = self._native_task_id(task_id)
        normalized_execution_id = self._native_execution_id(execution_id)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET requested_action = ?, resolved_action = ?, resolved_target = ?,
                    status = ?, execution_id = '', task_runner_id = '', last_error_code = ?,
                    last_error_message = ?,
                    synced_at = CASE WHEN ? IN ('synced', 'already_synced')
                        THEN CURRENT_TIMESTAMP ELSE synced_at END
                WHERE list_kind = ? AND item_key = ? AND task_id = ?
                  AND status = 'syncing' AND execution_id = ?
                """,
                (
                    requested_action,
                    resolved_action,
                    resolved_target,
                    status,
                    last_error_code,
                    last_error_message,
                    status,
                    normalized_kind,
                    normalized_key,
                    normalized_task_id,
                    normalized_execution_id,
                ),
            )
            completed = int(cursor.rowcount or 0) > 0
            if completed:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET requested_action = ?, resolved_action = ?, resolved_target = ?,
                        status = ?, is_live = 0, last_error_code = ?,
                        last_error_message = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status = 'syncing'
                    """,
                    (
                        requested_action,
                        resolved_action,
                        resolved_target,
                        status,
                        last_error_code,
                        last_error_message,
                        normalized_task_id,
                        normalized_key,
                    ),
                )
            conn.commit()
            return completed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reconcile_stale_native_save_claims(
        self,
        task_id: str,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Turn abandoned syncing leases into explicit retryable failures."""
        normalized_task_id = self._native_task_id(task_id)
        age = max(0, int(stale_after_seconds))
        params = (normalized_task_id, f"-{age} seconds")
        where_sql = """
            WHERE task_id = ? AND status = 'syncing'
              AND last_attempt_at IS NOT NULL
              AND last_attempt_at <= datetime('now', ?)
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (normalized_task_id, str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET status = 'failed', execution_id = '', task_runner_id = '',
                    last_error_code = 'interrupted',
                    last_error_message = 'Native save was interrupted'
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reconcile_stale_native_save_claims_for_list(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None = None,
        *,
        stale_after_seconds: int = 300,
    ) -> int:
        """Recover stale syncing rows selected by a normal manual-list action."""
        normalized_kind = self._saved_list_kind(list_kind)
        raw_keys = list(item_keys or ())
        cleaned_keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if raw_keys and not cleaned_keys:
            raise ValueError("item_keys must contain at least one non-blank key")
        item_filter = ""
        params: list[Any] = [normalized_kind]
        if cleaned_keys:
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND item_key IN ({placeholders})"
            params.extend(cleaned_keys)
        age = max(0, int(stale_after_seconds))
        params.append(f"-{age} seconds")
        where_sql = (
            """
            WHERE list_kind = ? AND status = 'syncing'
            """
            + item_filter
            + """
              AND last_attempt_at IS NOT NULL
              AND last_attempt_at <= datetime('now', ?)
            """
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT task_id, item_key FROM native_save_states " + where_sql,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'interrupted',
                        last_error_message = 'Native save was interrupted',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                    """,
                    (str(row["task_id"]), str(row["item_key"])),
                )
            cursor = conn.execute(
                """
                UPDATE native_save_states
                SET status = 'failed', execution_id = '', task_runner_id = '',
                    last_error_code = 'interrupted',
                    last_error_message = 'Native save was interrupted'
                """
                + where_sql,
                params,
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_native_sync_eligible(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List memberships eligible for an initial sync or a manual retry."""
        normalized_kind = self._saved_list_kind(list_kind)
        params: list[Any] = [normalized_kind]
        item_filter = ""
        if item_keys:
            cleaned_keys = [item_key.strip() for item_key in item_keys]
            placeholders = ", ".join("?" for _ in cleaned_keys)
            item_filter = f" AND m.item_key IN ({placeholders})"
            params.extend(cleaned_keys)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            self._saved_membership_select()
            + """
            WHERE m.list_kind = ?
              AND (
                  n.status IS NULL
                  OR (n.status = 'pending' AND n.task_id = '')
                  OR n.status IN (
                      'login_required', 'rate_limited',
                      'extension_required', 'failed'
                  )
                  OR (
                      n.status = 'unsupported'
                      AND n.last_error_code = 'unsupported_adapter_missing'
                  )
              )
            """
            + item_filter
            + " ORDER BY m.added_at DESC, m.item_key ASC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def list_native_save_states_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """Return task states in snapshot order, with legacy-order fallback."""
        normalized_task_id = self._native_task_id(task_id)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT
                n.list_kind,
                n.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                i.title,
                i.author_name,
                i.cover_url,
                m.note,
                m.added_at,
                n.requested_action,
                n.resolved_action,
                n.resolved_target,
                n.status,
                n.task_id,
                n.execution_id,
                n.task_claimed_at,
                n.task_started_at,
                n.task_heartbeat_at,
                n.task_runner_id,
                n.last_error_code,
                n.last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM native_save_states AS n
            LEFT JOIN native_save_task_items AS task_item
                ON task_item.task_id = n.task_id AND task_item.item_key = n.item_key
            JOIN saved_memberships AS m
                ON m.list_kind = n.list_kind AND m.item_key = n.item_key
            JOIN saved_items AS i ON i.item_key = n.item_key
            WHERE n.task_id = ?
            ORDER BY
                CASE WHEN task_item.ordinal IS NULL THEN 1 ELSE 0 END ASC,
                task_item.ordinal ASC,
                m.added_at DESC,
                n.item_key ASC
            """,
            (normalized_task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── Auth state (password gate revocation epoch) ──────────────

    def _ensure_auth_state_table(self) -> None:
        """Create the auth_state key/value table.

        Holds the global revocation epoch (``auth_epoch``) and the password
        fingerprint, kept out of ``config.toml`` so that revocation is a
        cross-process atomic counter rather than a whole-file rewrite. See
        ``docs/plans/2026-05-30-web-password-auth-design.md`` §4.7.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS auth_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def _ensure_init_runs_table(self) -> None:
        """Create the init_runs table backing guided (GUI) initialization.

        One row per guided-init run; the latest row is the authoritative
        progress source for ``GET /api/init-status`` (docs/specs/gui-init.md
        §5a). State survives restarts so a crashed / hot-reloaded run is
        reconciled to ``failed`` on boot rather than leaving a stuck
        ``running`` flag.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS init_runs (
                run_id          TEXT PRIMARY KEY,
                -- status: idle|starting|running|completed|failed|cancelled
                status          TEXT NOT NULL,
                stage           INTEGER NOT NULL DEFAULT 0,  -- 0..4
                stages_json     TEXT,  -- JSON: per-stage [{n,status,reason}]
                partial_success INTEGER NOT NULL DEFAULT 0,
                error_reason    TEXT,
                -- Human-readable failure specifics (exception summary /
                -- GuidedInitError message) surfaced by /api/init-status so
                -- an internal_error is diagnosable without server logs.
                error_detail    TEXT,
                sequence        INTEGER NOT NULL DEFAULT 0,
                -- ``updated_at`` is the worker heartbeat; these two fields
                -- advance only when a lifecycle milestone or real unit of
                -- work completes. Keeping them separate lets clients tell a
                -- live-but-slow provider call from a dead backend.
                progress_sequence INTEGER NOT NULL DEFAULT 0,
                progress_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at     TIMESTAMP
            );
        """)
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(init_runs)").fetchall()
        }
        if "error_detail" not in existing_columns:
            self.conn.execute("ALTER TABLE init_runs ADD COLUMN error_detail TEXT")
        if "progress_sequence" not in existing_columns:
            self.conn.execute(
                "ALTER TABLE init_runs ADD COLUMN progress_sequence INTEGER NOT NULL DEFAULT 0"
            )
        if "progress_at" not in existing_columns:
            self.conn.execute("ALTER TABLE init_runs ADD COLUMN progress_at TIMESTAMP")
            self.conn.execute(
                "UPDATE init_runs SET progress_at = COALESCE(updated_at, CURRENT_TIMESTAMP) "
                "WHERE progress_at IS NULL"
            )

    def get_latest_init_run(self) -> dict[str, Any] | None:
        """Return the most recent init run as a dict, or None if none exist.

        Reads fresh WAL state so a run written by the background task / another
        process is visible immediately.
        """
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT * FROM init_runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None

    def try_reserve_init_starting(self, run_id: str) -> bool:
        """Atomically reserve a new init run in ``starting`` state.

        Single-flight via ``BEGIN IMMEDIATE`` CAS (like ``bump_auth_epoch``):
        succeeds only when no run is currently ``starting``/``running``.
        Returns False when an init is already active, so concurrent
        ``POST /api/init`` callers cannot double-start (spec §5b TOCTOU).
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT 1 FROM init_runs WHERE status IN ('starting','running') LIMIT 1"
            ).fetchone()
            if active is not None:
                conn.rollback()
                return False
            conn.execute(
                """
                INSERT INTO init_runs (
                    run_id, status, stage, sequence, progress_sequence,
                    progress_at, started_at, updated_at
                )
                VALUES (?, 'starting', 0, 0, 0,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    status='starting', stage=0, sequence=0, progress_sequence=0,
                    error_reason=NULL, error_detail=NULL, finished_at=NULL,
                    progress_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                """,
                (run_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def update_init_run(self, run_id: str, **fields: Any) -> None:
        """Update mutable columns of an init run (the single status writer).

        Only whitelisted columns are accepted and ``updated_at`` is always
        bumped; unknown keys raise so a typo cannot silently no-op.
        """
        allowed = {
            "status",
            "stage",
            "stages_json",
            "partial_success",
            "error_reason",
            "error_detail",
            "sequence",
            "progress_sequence",
            "progress_at",
            "finished_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"update_init_run: unknown columns {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{col} = ?" for col in fields)
        params = [*fields.values(), run_id]
        self._execute_write(
            f"UPDATE init_runs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            params,
        )

    def reconcile_init_runs_on_boot(self) -> int:
        """Fail any run left ``starting``/``running`` by a crash/restart.

        No init task survives a process restart, so a persisted active status
        is necessarily stale. Returns the number of rows reconciled (spec §5a).

        Mirrors ``InitCoordinator.reconcile_orphaned_run``: besides flipping the
        row status, it downgrades any ``running``/``pending`` stage inside
        ``stages_json`` to ``failed``/``interrupted`` and writes a user-facing
        Chinese ``error_detail`` so ``GET /api/init-status`` no longer reports a
        phantom "running" stage with an empty detail after a mid-init restart.
        """
        rows = self.conn.execute(
            "SELECT run_id, stages_json FROM init_runs WHERE status IN ('starting','running')"
        ).fetchall()
        if not rows:
            return 0
        for row in rows:
            stages_raw = row["stages_json"]
            stages = json.loads(stages_raw) if stages_raw else []
            for stage in stages:
                if stage.get("status") in ("running", "pending"):
                    stage["status"] = "failed"
                    stage["reason"] = "interrupted"
                    stage.pop("progress", None)
            self._execute_write(
                """
                UPDATE init_runs
                   SET status = 'failed', error_reason = 'interrupted',
                       error_detail = ?, stages_json = ?,
                       sequence = sequence + 1,
                       progress_sequence = sequence + 1,
                       progress_at = CURRENT_TIMESTAMP,
                       finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                 WHERE run_id = ?
                """,
                (
                    "初始化后台任务已结束，但未能写入终态；已自动释放运行锁。",
                    json.dumps(stages, ensure_ascii=False),
                    row["run_id"],
                ),
            )
        return len(rows)

    def get_auth_epoch(self) -> int:
        """Return the current revocation epoch. Reads fresh WAL state.

        A missing row means "never bumped" → 0. A present-but-corrupt value
        RAISES (never silently 0) so the auth gate fails closed instead of
        resurrecting tokens minted before a prior revocation. See §4.7.
        """
        self._ensure_fresh_read()
        row = self.conn.execute("SELECT value FROM auth_state WHERE key = 'auth_epoch'").fetchone()
        if row is None:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"corrupt auth_epoch value: {row[0]!r}") from exc

    def _set_browser_login_state(
        self,
        *,
        state_key: str,
        timestamp_key: str,
        logged_in: bool,
        when_iso: str,
    ) -> None:
        """Persist a browser heartbeat on an isolated FastAPI-safe connection."""
        conn = self.open_connection()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
                [
                    (state_key, "1" if logged_in else "0"),
                    (timestamp_key, when_iso),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _get_browser_login_state(
        self,
        *,
        state_key: str,
        timestamp_key: str,
    ) -> tuple[bool, str]:
        """Read a browser heartbeat without sharing the process connection."""
        conn = self.open_connection()
        try:
            rows = conn.execute(
                "SELECT key, value FROM auth_state WHERE key IN (?, ?)",
                (state_key, timestamp_key),
            ).fetchall()
        finally:
            conn.close()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        state = values.get(state_key)
        when_iso = values.get(timestamp_key, "").strip()
        if state not in {"0", "1"} or not when_iso:
            return False, ""
        return state == "1", when_iso

    def set_xhs_login_state(self, logged_in: bool, when_iso: str | None = None) -> None:
        """Persist the latest browser-observed xhs login state.

        The browser extension deliberately sends only this boolean, never the
        ``web_session`` cookie value, because xhs fetching remains client-side.
        """
        if not isinstance(logged_in, bool):
            raise TypeError("logged_in must be bool")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        self._set_browser_login_state(
            state_key="xhs_login_state",
            timestamp_key="xhs_login_state_at",
            logged_in=logged_in,
            when_iso=str(when_iso),
        )

    def get_xhs_login_state(self) -> tuple[bool, str]:
        """Return ``(logged_in, iso_timestamp)`` for xhs, or ``(False, "")``."""
        return self._get_browser_login_state(
            state_key="xhs_login_state",
            timestamp_key="xhs_login_state_at",
        )

    def set_zhihu_login_state(self, logged_in: bool, when_iso: str | None = None) -> None:
        """Persist the latest browser-observed Zhihu login state.

        The browser extension sends only whether ``z_c0`` is present and
        non-empty; it never sends the cookie value.
        """
        if not isinstance(logged_in, bool):
            raise TypeError("logged_in must be bool")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        self._set_browser_login_state(
            state_key="zhihu_login_state",
            timestamp_key="zhihu_login_state_at",
            logged_in=logged_in,
            when_iso=str(when_iso),
        )

    def get_zhihu_login_state(self) -> tuple[bool, str]:
        """Return ``(logged_in, iso_timestamp)`` for Zhihu, or ``(False, "")``."""
        return self._get_browser_login_state(
            state_key="zhihu_login_state",
            timestamp_key="zhihu_login_state_at",
        )

    def set_linuxdo_login_state(self, logged_in: bool, when_iso: str | None = None) -> None:
        """Persist whether the extension observed Linux.do's real ``_t`` session."""
        if not isinstance(logged_in, bool):
            raise TypeError("logged_in must be bool")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        self._set_browser_login_state(
            state_key="linuxdo_login_state",
            timestamp_key="linuxdo_login_state_at",
            logged_in=logged_in,
            when_iso=str(when_iso),
        )

    def set_v2ex_login_state(self, logged_in: bool, when_iso: str | None = None) -> None:
        """Persist the privacy-preserving V2EX browser login heartbeat."""
        if not isinstance(logged_in, bool):
            raise TypeError("logged_in must be bool")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        self._set_browser_login_state(
            state_key="v2ex_login_state",
            timestamp_key="v2ex_login_state_at",
            logged_in=logged_in,
            when_iso=str(when_iso),
        )

    def get_linuxdo_login_state(self) -> tuple[bool, str]:
        """Return ``(logged_in, iso_timestamp)`` for Linux.do, or ``(False, "")``."""
        return self._get_browser_login_state(
            state_key="linuxdo_login_state",
            timestamp_key="linuxdo_login_state_at",
        )

    def get_v2ex_login_state(self) -> tuple[bool, str]:
        """Return ``(logged_in, iso_timestamp)`` for V2EX."""
        return self._get_browser_login_state(
            state_key="v2ex_login_state",
            timestamp_key="v2ex_login_state_at",
        )

    def set_weibo_login_state(self, logged_in: bool, when_iso: str | None = None) -> None:
        """Persist only the browser-observed Weibo login boolean.

        The extension never sends a Weibo Cookie or visitor ``SUB`` value;
        the actual account request runs inside the logged-in browser tab.
        """
        if not isinstance(logged_in, bool):
            raise TypeError("logged_in must be bool")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        self._set_browser_login_state(
            state_key="weibo_login_state",
            timestamp_key="weibo_login_state_at",
            logged_in=logged_in,
            when_iso=str(when_iso),
        )

    def get_weibo_login_state(self) -> tuple[bool, str]:
        """Return ``(logged_in, iso_timestamp)`` for Weibo."""
        return self._get_browser_login_state(
            state_key="weibo_login_state",
            timestamp_key="weibo_login_state_at",
        )

    def set_v2ex_browser_identity(
        self,
        username: str,
        *,
        evidence: str = "observed",
        when_iso: str | None = None,
    ) -> None:
        """Persist an observed V2EX username without claiming verification."""
        normalized = str(username or "").strip()
        if not normalized or len(normalized) > 128 or any(ord(c) < 32 for c in normalized):
            raise ValueError("V2EX username is invalid")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        conn = self.open_connection()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
                [
                    ("v2ex_browser_username", normalized),
                    ("v2ex_browser_identity_evidence", str(evidence or "observed")),
                    ("v2ex_browser_identity_at", str(when_iso)),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def get_v2ex_browser_identity(self) -> tuple[str, str, str]:
        """Return ``(username, evidence, observed_at)`` for V2EX."""
        conn = self.open_connection()
        try:
            rows = conn.execute(
                """
                SELECT key, value FROM auth_state
                WHERE key IN (
                    'v2ex_browser_username',
                    'v2ex_browser_identity_evidence',
                    'v2ex_browser_identity_at'
                )
                """
            ).fetchall()
        finally:
            conn.close()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        return (
            values.get("v2ex_browser_username", "").strip(),
            values.get("v2ex_browser_identity_evidence", "").strip() or "observed",
            values.get("v2ex_browser_identity_at", "").strip(),
        )

    def clear_v2ex_browser_identity(self) -> None:
        """Forget an observed browser account after an explicit logout heartbeat."""
        conn = self.open_connection()
        try:
            conn.execute(
                """
                DELETE FROM auth_state
                WHERE key IN (
                    'v2ex_browser_username',
                    'v2ex_browser_identity_evidence',
                    'v2ex_browser_identity_at'
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def set_v2ex_pat_identity(
        self,
        username: str,
        *,
        credential_fingerprint: str,
        when_iso: str | None = None,
    ) -> None:
        """Persist only the account claim proven by the current PAT.

        The token itself is never stored here. Its one-way credential
        fingerprint binds this public username to the exact configured PAT so
        changing or removing the token makes the old claim ineligible.
        """
        normalized = str(username or "").strip()
        fingerprint = str(credential_fingerprint or "").strip()
        if not normalized or len(normalized) > 128 or any(ord(c) < 32 for c in normalized):
            raise ValueError("V2EX username is invalid")
        if not fingerprint or len(fingerprint) > 256:
            raise ValueError("V2EX credential fingerprint is invalid")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        conn = self.open_connection()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
                [
                    ("v2ex_pat_username", normalized),
                    ("v2ex_pat_credential_fingerprint", fingerprint),
                    ("v2ex_pat_identity_at", str(when_iso)),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def get_v2ex_pat_identity(self) -> tuple[str, str, str]:
        """Return ``(username, credential_fingerprint, verified_at)``."""
        conn = self.open_connection()
        try:
            rows = conn.execute(
                """
                SELECT key, value FROM auth_state
                WHERE key IN (
                    'v2ex_pat_username',
                    'v2ex_pat_credential_fingerprint',
                    'v2ex_pat_identity_at'
                )
                """
            ).fetchall()
        finally:
            conn.close()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        return (
            values.get("v2ex_pat_username", "").strip(),
            values.get("v2ex_pat_credential_fingerprint", "").strip(),
            values.get("v2ex_pat_identity_at", "").strip(),
        )

    def clear_v2ex_pat_identity(self, *, credential_fingerprint: str = "") -> bool:
        """Forget the PAT account claim, optionally only for one exact PAT digest."""
        expected = str(credential_fingerprint or "").strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if expected:
                row = conn.execute(
                    "SELECT value FROM auth_state WHERE key='v2ex_pat_credential_fingerprint'"
                ).fetchone()
                current = str(row[0] if row is not None else "").strip()
                if current != expected:
                    conn.commit()
                    return False
            cursor = conn.execute(
                """
                DELETE FROM auth_state
                WHERE key IN (
                    'v2ex_pat_username',
                    'v2ex_pat_credential_fingerprint',
                    'v2ex_pat_identity_at'
                )
                """
            )
            conn.commit()
            return int(cursor.rowcount) > 0
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def set_v2ex_accepted_identity(self, username: str, when_iso: str | None = None) -> None:
        """Persist a username the user explicitly selected during conflict resolution."""
        normalized = str(username or "").strip()
        if not normalized or len(normalized) > 128 or any(ord(c) < 32 for c in normalized):
            raise ValueError("V2EX username is invalid")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        conn = self.open_connection()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
                [
                    ("v2ex_accepted_username", normalized),
                    ("v2ex_accepted_identity_at", str(when_iso)),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def get_v2ex_accepted_identity(self) -> tuple[str, str]:
        """Return ``(username, accepted_at)`` for an explicit user choice."""
        conn = self.open_connection()
        try:
            rows = conn.execute(
                """
                SELECT key, value FROM auth_state
                WHERE key IN ('v2ex_accepted_username', 'v2ex_accepted_identity_at')
                """
            ).fetchall()
        finally:
            conn.close()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        return (
            values.get("v2ex_accepted_username", "").strip(),
            values.get("v2ex_accepted_identity_at", "").strip(),
        )

    def get_v2ex_profile_identity(self) -> tuple[str, str]:
        """Return the account whose V2EX evidence currently owns the profile."""

        conn = self.open_connection()
        try:
            rows = conn.execute(
                """
                SELECT key, value FROM auth_state
                WHERE key IN ('v2ex_profile_username', 'v2ex_profile_identity_at')
                """
            ).fetchall()
        finally:
            conn.close()
        values = {str(row["key"]): str(row["value"]) for row in rows}
        return (
            values.get("v2ex_profile_username", "").strip(),
            values.get("v2ex_profile_identity_at", "").strip(),
        )

    def activate_v2ex_profile_identity(
        self,
        username: str,
        *,
        when_iso: str | None = None,
    ) -> dict[str, Any]:
        """Atomically activate one V2EX account and isolate every other one.

        Historical rows are retained for audit/export.  Their generic profile
        owner marker is removed and ``profile_inactive`` is set, so a future
        cursor recovery or profile rebuild cannot silently merge two accounts.
        Re-selecting an older account makes its rows readable again; guided init
        is still responsible for rebuilding the current Soul before this method
        is called during an account switch.
        """

        normalized = str(username or "").strip()
        if not normalized or len(normalized) > 128 or any(ord(c) < 32 for c in normalized):
            raise ValueError("V2EX username is invalid")
        if when_iso is None:
            from datetime import UTC, datetime

            when_iso = datetime.now(UTC).isoformat()
        identity_key = normalized.casefold()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous_row = conn.execute(
                "SELECT value FROM auth_state WHERE key='v2ex_profile_username'"
            ).fetchone()
            previous = str(previous_row[0] if previous_row is not None else "").strip()
            rows = conn.execute(
                "SELECT id, metadata FROM events WHERE metadata LIKE '%v2ex%'"
            ).fetchall()
            activated = 0
            deactivated = 0
            for row in rows:
                try:
                    metadata = json.loads(str(row["metadata"] or "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                if (
                    not isinstance(metadata, dict)
                    or str(metadata.get("source_platform") or "").casefold() != "v2ex"
                ):
                    continue
                row_identity = str(metadata.get("source_identity") or "").strip()
                # Passive dwell/local-feedback rows are deliberately not tied
                # to a V2EX account. They remain valid local evidence when the
                # browser account changes and must never be hidden as if they
                # belonged to the previous account bootstrap.
                if not row_identity:
                    continue
                is_active = row_identity.casefold() == identity_key
                changed = False
                if is_active:
                    if metadata.pop("profile_inactive", None) is not None:
                        changed = True
                    if metadata.pop("identity_deactivated_at", None) is not None:
                        changed = True
                    if changed:
                        activated += 1
                else:
                    if metadata.get("profile_inactive") is not True:
                        metadata["profile_inactive"] = True
                        changed = True
                    if metadata.get("identity_deactivated_at") != str(when_iso):
                        metadata["identity_deactivated_at"] = str(when_iso)
                        changed = True
                    if metadata.pop("profile_update_owner", None) is not None:
                        changed = True
                    if changed:
                        deactivated += 1
                if changed:
                    conn.execute(
                        "UPDATE events SET metadata=? WHERE id=?",
                        (json.dumps(metadata, ensure_ascii=False), int(row["id"])),
                    )
            conn.executemany(
                "INSERT OR REPLACE INTO auth_state (key, value) VALUES (?, ?)",
                [
                    ("v2ex_profile_username", normalized),
                    ("v2ex_profile_identity_at", str(when_iso)),
                ],
            )
            conn.commit()
            return {
                "previous_username": previous,
                "username": normalized,
                "activated_events": activated,
                "deactivated_events": deactivated,
            }
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def bump_auth_epoch(self) -> int:
        """Atomically increment and return the revocation epoch.

        Uses a short-lived connection with ``BEGIN IMMEDIATE`` so concurrent
        bumps (or another process) cannot lose an increment.
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM auth_state WHERE key = 'auth_epoch'").fetchone()
            # Missing → 0; corrupt → raise (never reset a damaged epoch downward).
            current = 0 if row is None else int(row[0])
            new_value = current + 1
            conn.execute(
                """
                INSERT INTO auth_state (key, value) VALUES ('auth_epoch', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(new_value),),
            )
            conn.commit()
            return new_value
        finally:
            conn.close()

    def reconcile_password_fingerprint(self, fingerprint: str) -> bool:
        """Detect a password change and bump the epoch if needed.

        Compares ``fingerprint`` (derived from stable credential material, see
        ``auth_core.password_fingerprint``) against the stored value, inside a
        single ``BEGIN IMMEDIATE`` transaction (CAS). Returns ``True`` when the
        epoch was bumped. First enable (no prior fingerprint) records it WITHOUT
        bumping. See §4.7.
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
            ).fetchone()
            stored = row[0] if row is not None else None
            bumped = False
            if stored is None:
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
            elif stored != fingerprint:
                epoch_row = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
                ).fetchone()
                # Missing → 0; corrupt → raise (the caller fails closed).
                current = 0 if epoch_row is None else int(epoch_row[0])
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', ?)",
                    (str(current + 1),),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
                bumped = True
            conn.commit()
            return bumped
        finally:
            conn.close()

    def set_password_fingerprint(self, fingerprint: str) -> None:
        """Overwrite the stored fingerprint without touching the epoch.

        Used after ``--rotate-secret`` re-bases the fingerprint under a new
        signing secret, so the next reconcile does not double-bump.
        """
        self._execute_write(
            "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('password_fingerprint', ?)",
            (fingerprint,),
        )

    def revoke_and_set_fingerprint(self, fingerprint: str | None, *, force_bump: bool) -> None:
        """Atomically (single ``BEGIN IMMEDIATE``) set the fingerprint, bumping the
        epoch when the credential changed or ``force_bump`` is set.

        Used by the local admin endpoint so a password change's revocation
        (epoch bump) and fingerprint update commit together — never a half state
        where the new password is live but old sessions survive (review r1#2).

        The bump decision is made INSIDE the transaction by comparing ``fingerprint``
        to the stored one (CAS), mirroring ``reconcile_password_fingerprint``: a
        first-ever set (no stored fingerprint) never bumps, but any *change* from an
        existing fingerprint always does — even when the caller's ``force_bump`` is
        false. This catches an effective credential change the caller can't see in
        its request, e.g. admin hot-publishing a ``password_hash`` that drifted on
        disk via an out-of-band ``set-password`` (review r4#2). ``force_bump`` adds a
        revoke for enabled on/off toggles, which carry no fingerprint change.

        Raises on a corrupt epoch (caller fails closed). The caller persists the new
        config FIRST (rolling it back if this raises) and publishes to the live gate
        only AFTER this commits, so a failure here leaves the durable DB state
        untouched and the persisted/live auth on the old password; a crash between
        the config write and this call is healed by the startup fingerprint
        reconcile (review r2#1).
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            stored_row = conn.execute(
                "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
            ).fetchone()
            stored = stored_row[0] if stored_row is not None else None
            credential_changed = (
                fingerprint is not None and stored is not None and stored != fingerprint
            )
            if force_bump or credential_changed:
                row = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
                ).fetchone()
                current = 0 if row is None else int(row[0])  # corrupt → raise
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', ?)",
                    (str(current + 1),),
                )
            if fingerprint is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
            conn.commit()
        finally:
            conn.close()

    # ── Favorites CRUD ───────────────────────────────────────────

    def add_to_favorites(self, bvid: str, note: str = "") -> bool:
        """Save a video to favorites. Returns True if newly inserted."""
        item = self._bilibili_saved_item_input(bvid)
        self.upsert_saved_membership("favorite", item, note)
        self._execute_write(
            """
            INSERT INTO favorites (bvid, note, item_key)
            VALUES (?, ?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note,
                item_key = excluded.item_key
            """,
            (bvid.strip(), note, item.item_key),
        )
        return self.conn.total_changes > 0

    def remove_from_favorites(self, bvid: str) -> bool:
        """Remove a favorite. Returns True if a row was deleted."""
        item_key = self._resolve_legacy_saved_item_key("favorite", bvid)
        if item_key is None:
            return False
        return self.remove_saved_membership("favorite", item_key)

    def is_in_favorites(self, bvid: str) -> bool:
        """Check whether a video is favorited."""
        item_key = self._resolve_legacy_saved_item_key("favorite", bvid)
        return item_key is not None and self.get_saved_membership("favorite", item_key) is not None

    def count_favorites(self) -> int:
        """Return total number of favorited videos."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM saved_memberships WHERE list_kind = ?",
            ("favorite",),
        ).fetchone()
        return int(row[0]) if row else 0

    def list_favorites(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return favorited videos with content_cache metadata, newest first."""
        return [
            {
                "bvid": row["content_id"],
                "item_key": row["item_key"],
                "content_id": row["content_id"],
                "added_at": row["added_at"],
                "note": row["note"],
                "title": row["title"],
                "up_name": row["author_name"],
                "cover_url": row["cover_url"],
                "content_url": row["content_url"],
                "source_platform": row["source_platform"],
                "content_type": row["content_type"],
            }
            for row in self.list_saved_memberships("favorite", limit, offset)
        ]

    def iter_cover_lifecycle(self) -> list[tuple[str, str, bool]]:
        """Return ``(cover_url, pool_status, is_saved)`` for every cached-cover candidate.

        ``is_saved`` is True when the canonical item key has a normalized saved
        membership, with legacy Bilibili tables retained as a compatibility fallback.
        Consumed by the image-cache cleanup (:mod:`openbiliclaw.runtime.image_cache`)
        to decide which cached cover files are safe to evict: covers of saved or
        still-pending content are kept; covers of consumed, unsaved content are
        eligible for removal.
        """
        cursor = self.conn.execute(
            """
            SELECT
                COALESCE(cc.cover_url, '') AS cover_url,
                COALESCE(cc.pool_status, 'fresh') AS pool_status,
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM saved_memberships AS m
                    WHERE m.item_key = cc.item_key
                ) OR f.bvid IS NOT NULL OR w.bvid IS NOT NULL THEN 1 ELSE 0 END AS is_saved
            FROM content_cache AS cc
            LEFT JOIN favorites AS f ON f.bvid = cc.bvid
            LEFT JOIN watch_later AS w ON w.bvid = cc.bvid
            WHERE COALESCE(cc.cover_url, '') <> ''
            """
        )
        return [
            (str(row["cover_url"]), str(row["pool_status"]), bool(row["is_saved"]))
            for row in cursor.fetchall()
        ]

    def iter_servable_cover_urls(self, *, recent_hours: int = 12, limit: int = 300) -> list[str]:
        """Recent, still-servable cover URLs (newest first) for discovery-time prefetch.

        Returns covers of content that may still be shown — ``pool_status`` in
        ``fresh / shown / suppressed``, or saved (favorites / watch_later) — limited
        to the last ``recent_hours`` of discoveries and ordered newest-first, so the
        prefetch sweep (:mod:`openbiliclaw.runtime.image_cache`) caches the freshest
        CDN tokens (notably XHS) before they expire. The recency window also keeps the
        sweep from endlessly retrying old content whose signed token is already dead.
        """
        cursor = self.conn.execute(
            """
            SELECT cc.cover_url
            FROM content_cache AS cc
            LEFT JOIN favorites AS f ON f.bvid = cc.bvid
            LEFT JOIN watch_later AS w ON w.bvid = cc.bvid
            WHERE COALESCE(cc.cover_url, '') <> ''
              AND cc.discovered_at >= datetime('now', ?)
              AND (
                COALESCE(cc.pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
                OR EXISTS (
                    SELECT 1
                    FROM saved_memberships AS m
                    WHERE m.item_key = cc.item_key
                )
                OR f.bvid IS NOT NULL
                OR w.bvid IS NOT NULL
              )
            ORDER BY cc.discovered_at DESC
            LIMIT ?
            """,
            (f"-{int(recent_hours)} hours", limit),
        )
        return [str(row["cover_url"]) for row in cursor.fetchall()]

    # ── XHS observed URL ingest ───────────────────────────────────

    def save_xhs_observed_urls(self, urls: list[str], page_type: str) -> int:
        """Insert observed xhs URLs, skipping duplicates. Returns count inserted."""
        inserted = 0
        for url in urls:
            # Skip if we've already seen this URL
            existing = self.conn.execute(
                "SELECT 1 FROM xhs_observed_urls WHERE url = ?", (url,)
            ).fetchone()
            if existing:
                continue
            self._execute_write(
                "INSERT INTO xhs_observed_urls (url, page_type) VALUES (?, ?)",
                (url, page_type),
            )
            inserted += 1
        return inserted

    # ── Source recipe CRUD ──────────────────────────────────────────

    def save_source_recipe(self, recipe: dict[str, Any]) -> None:
        """Insert or update a source recipe."""
        import json as _json

        self._execute_write(
            """
            INSERT INTO source_recipes (id, source_type, name, strategy, config,
                                        target_share, enabled, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                strategy = excluded.strategy,
                config = excluded.config,
                target_share = excluded.target_share,
                enabled = excluded.enabled
            """,
            (
                str(recipe["id"]),
                str(recipe["source_type"]),
                str(recipe["name"]),
                str(recipe["strategy"]),
                _json.dumps(recipe.get("config", {}), ensure_ascii=False),
                int(recipe.get("target_share", 4)),
                int(recipe.get("enabled", True)),
                str(recipe.get("created_by", "system")),
                recipe.get("created_at") or None,
            ),
        )

    def get_all_recipes(self) -> list[dict[str, Any]]:
        """Return all source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute("SELECT * FROM source_recipes ORDER BY created_at").fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def get_enabled_recipes(self) -> list[dict[str, Any]]:
        """Return only enabled source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute(
            "SELECT * FROM source_recipes WHERE enabled = 1 ORDER BY created_at"
        ).fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def update_recipe(self, recipe_id: str, **fields: Any) -> bool:
        """Update specific fields of a recipe. Returns True if a row was updated."""
        import json as _json

        allowed = {"name", "strategy", "config", "target_share", "enabled", "last_fetched_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        if "config" in updates and not isinstance(updates["config"], str):
            updates["config"] = _json.dumps(updates["config"], ensure_ascii=False)
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [recipe_id]
        cursor = self._execute_write(
            f"UPDATE source_recipes SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        return cursor.rowcount > 0

    def delete_recipe(self, recipe_id: str) -> bool:
        """Delete a recipe by id. Returns True if a row was deleted."""
        cursor = self._execute_write(
            "DELETE FROM source_recipes WHERE id = ?",
            (recipe_id,),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_recipe(row: Any) -> dict[str, Any]:
        import json as _json

        config_raw = row["config"] if row["config"] else "{}"
        try:
            config = _json.loads(config_raw)
        except (ValueError, TypeError):
            config = {}
        return {
            "id": str(row["id"]),
            "source_type": str(row["source_type"]),
            "name": str(row["name"]),
            "strategy": str(row["strategy"]),
            "config": config,
            "target_share": int(row["target_share"]),
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"] or ""),
            "last_fetched_at": str(row["last_fetched_at"] or ""),
        }

    def dynamic_delight_threshold(
        self,
        *,
        default_threshold: float = _DELIGHT_CLAIM_MIN_SCORE,
    ) -> float:
        """Return the profile floor raised to the delight pool Top 10% boundary.

        The dynamic component uses the current formal candidate pool, not raw
        ``discovery_candidates``. The percentile is computed over rows that
        already have ``delight_score``. When the scored pool is too small or
        too homogeneous for a meaningful percentile, the caller-provided
        default is returned unchanged.
        """
        self._ensure_fresh_read()
        return self._dynamic_delight_threshold_on(
            self.conn,
            default_threshold=default_threshold,
        )

    def _dynamic_delight_threshold_on(
        self,
        conn: sqlite3.Connection,
        *,
        default_threshold: float,
    ) -> float:
        """Connection-aware dynamic delight threshold calculation."""
        try:
            floor = float(default_threshold)
        except (TypeError, ValueError):
            floor = _DELIGHT_CLAIM_MIN_SCORE
        floor = min(1.0, max(0.0, floor))

        cursor = conn.execute(
            f"""
            SELECT COALESCE(delight_score, 0.0) AS score,
                   published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(delight_score, 0.0) > 0.0
              {_delight_ready_copy_sql()}
              {_delight_unseen_guard_sql()}
            ORDER BY score DESC
            """
        )
        temporal_now = datetime.now(UTC)
        scores = [
            float(row["score"])
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        if len(scores) < _DELIGHT_DYNAMIC_MIN_SAMPLE_SIZE:
            return floor
        if statistics.pstdev(scores) < _DELIGHT_DYNAMIC_MIN_STDDEV:
            return floor

        top_count = max(1, math.ceil(len(scores) * _DELIGHT_DYNAMIC_TOP_FRACTION))
        boundary = min(1.0, max(0.0, scores[top_count - 1]))
        return max(floor, boundary)

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 1,
    ) -> dict[str, Any] | None:
        """Return one un-notified pool item with the highest delight_score.

        Backwards-compatible: ``limit=1`` returns a single dict (or None);
        callers that want multiple candidates (for example to filter
        disliked topics in Python) should call
        ``get_delight_candidates`` instead.
        """
        rows = self.get_delight_candidates(
            min_delight_score=min_delight_score,
            limit=max(1, int(limit)),
        )
        return rows[0] if rows else None

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
        include_liked: bool = False,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` un-notified delight candidates ordered by score.

        Queue readiness is stricter than merely having a score and arbitrary
        non-empty metadata: ``pool_expression`` / ``pool_topic_label`` must be
        ready, and the persisted delight copy must be their synchronized
        snapshot.  This keeps evaluator-only ``relevance_reason`` text off all
        delight API and runtime-stream surfaces.

        Restricts to ``pool_status IN ('fresh', 'shown')`` —  ``suppressed``
        items have been trimmed out of the active pool by topic-group cap
        or source-share quota and shouldn't reappear as delights. Without
        this guard, popup re-hydration would pull historical delight
        scores baked under earlier (looser) calibrations from the
        suppressed graveyard and surface 20 stale "surprises" on every
        extension reload (observed 2026-05-04: 562 suppressed items
        carried delight metadata vs 2 in fresh).

        ``include_liked`` keeps ``feedback_type='like'`` rows in the result.
        Queue re-hydration (``/api/delight/pending-batch``) passes True so a
        liked delight stays visible until the user explicitly dismisses it —
        positive feedback must not remove the card (v0.3.63 contract). New
        delivery paths (WS push, counts, CLI) keep the default False so an
        already-liked item is never re-pushed as a fresh surprise.
        """
        feedback_clause = (
            "COALESCE(feedback_type, '') IN ('', 'like')"
            if include_liked
            else "COALESCE(feedback_type, '') = ''"
        )
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              {_delight_ready_copy_sql()}
              AND TRIM(COALESCE(delight_reason, '')) =
                  TRIM(COALESCE(pool_expression, ''))
              AND TRIM(COALESCE(delight_hook, '')) =
                  TRIM(COALESCE(pool_topic_label, ''))
              AND {feedback_clause}
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
              {_delight_unseen_guard_sql()}
            ORDER BY delight_score DESC, relevance_score DESC, discovered_at DESC
            """,
            (min_delight_score, *admission_params),
        )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        return rows[: max(1, int(limit))]

    def mark_delight_notified(self, bvid: str) -> None:
        """Mark one content item as delight-notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_notified = 1,
                delight_notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def mark_delight_seen(self, bvid: str) -> bool:
        """Permanently consume a delight and add its canonical identity to seen items.

        The seen ledger is written first, so a process interruption between the
        two writes still fails closed: every recommendation exit filters the
        canonical identity even if the delight-specific notification bit has
        not yet been updated.
        """
        row = self.conn.execute(
            """
            SELECT bvid, content_id, source, source_platform
            FROM content_cache
            WHERE bvid = ?
            LIMIT 1
            """,
            (bvid,),
        ).fetchone()
        recorded = False
        if row is not None:
            platform = canonical_source_platform(str(row["source_platform"] or ""))
            if not platform:
                platform = _pool_source_family(
                    str(row["source"] or ""),
                    str(row["source_platform"] or ""),
                )
            content_id = str(row["content_id"] or row["bvid"] or "").strip()
            if platform and platform != "unknown" and content_id:
                self.mark_items_seen(platform, [content_id])
                recorded = True
        self.mark_delight_notified(bvid)
        return recorded

    def update_delight_score(
        self,
        bvid: str,
        *,
        delight_score: float,
        delight_reason: str,
        delight_hook: str = "",
    ) -> bool:
        """Persist delight state only after formal recommendation copy is ready.

        Non-empty display metadata must be the exact current
        ``pool_expression`` / ``pool_topic_label`` snapshot. The conditional
        write is the durable admission boundary: an evaluator reason or an
        uncopied row cannot become delight state even if an upstream caller
        regresses.
        """
        reason = str(delight_reason or "").strip()
        hook = str(delight_hook or "").strip()
        if bool(reason) != bool(hook):
            logger.warning("Refusing partial delight copy for %s", bvid)
            return False

        snapshot_guard = ""
        params: tuple[Any, ...]
        if reason:
            snapshot_guard = """
              AND TRIM(COALESCE(pool_expression, '')) = ?
              AND TRIM(COALESCE(pool_topic_label, '')) = ?
            """
            params = (delight_score, reason, hook, bvid, reason, hook)
        else:
            params = (delight_score, reason, hook, bvid)

        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET delight_score = ?,
                delight_reason = ?,
                delight_hook = ?
            WHERE bvid = ?
              {_delight_ready_copy_sql()}
              {snapshot_guard}
            """,
            params,
        )
        return cursor.rowcount > 0

    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> int:
        """Return the number of copy-ready, un-notified delight candidates."""
        admission_sql, admission_params = self._pool_admission_sql()
        cursor = self.conn.execute(
            f"""
            SELECT published_at, temporal_class, temporal_confidence,
                   temporal_policy_version, temporal_validity_mode,
                   temporal_valid_until, temporal_scope, temporal_state, temporal_evidence,
                   temporal_next_review_at, temporal_evaluated_at,
                   temporal_evidence_complete
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              {_delight_ready_copy_sql()}
              AND TRIM(COALESCE(delight_reason, '')) =
                  TRIM(COALESCE(pool_expression, ''))
              AND TRIM(COALESCE(delight_hook, '')) =
                  TRIM(COALESCE(pool_topic_label, ''))
              AND COALESCE(feedback_type, '') = ''
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
              {_delight_unseen_guard_sql()}
            """,
            (min_delight_score, *admission_params),
        )
        temporal_now = datetime.now(UTC)
        return sum(
            1
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        )

    def get_pool_candidates_needing_delight_score(
        self,
        limit: int = 30,
        *,
        min_delight_score_for_reason: float | None = None,
        min_relevance_score: float = 0.55,
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Return copy-ready pool candidates that still need delight synchronization.

        Two-stage retrieval: ``relevance_score >= min_relevance_score``
        is the cheap pre-filter (the discovery LLM already judged user-
        content fit during ``evaluate_batch``), then the caller reuses that
        Evo relevance result to populate delight fields only on this
        shortlist. Rows with unfinished ``pool_expression`` /
        ``pool_topic_label`` stay exclusively in the copy backlog and are not
        assigned any delight state.

        Default 0.55 is calibrated to the discovery rubric:
          0.6+ strong fit, 0.5-0.6 moderate, <0.5 weak fit.
        Items below ``min_relevance_score`` skip delight backfill
        entirely — they're not going to delight anyone they don't
        already half-fit.
        """
        max_rows = max(0, int(limit))
        if max_rows == 0:
            return []
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        effective_min_relevance_score = _normalize_admission_min_score(min_relevance_score)
        if min_delight_score_for_reason is None:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(delight_score, 0.0) = 0.0
                  AND COALESCE(relevance_score, 0.0) >= ?
                  {_delight_ready_copy_sql()}
                  {guard_sql}
                  {_delight_unseen_guard_sql()}
                ORDER BY relevance_score DESC, discovered_at DESC
                """,
                (
                    effective_min_relevance_score,
                    *guard_params,
                ),
            )
        else:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  {_delight_ready_copy_sql()}
                  AND (
                    COALESCE(delight_score, 0.0) = 0.0
                    OR ABS(
                      COALESCE(delight_score, 0.0) - COALESCE(relevance_score, 0.0)
                    ) > ?
                    OR (
                      COALESCE(delight_score, 0.0) < ?
                      AND (
                        TRIM(COALESCE(delight_reason, '')) != ''
                        OR TRIM(COALESCE(delight_hook, '')) != ''
                      )
                    )
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND TRIM(COALESCE(pool_expression, '')) != ''
                      AND TRIM(COALESCE(pool_topic_label, '')) != ''
                      AND (
                        TRIM(COALESCE(delight_reason, '')) !=
                            TRIM(COALESCE(pool_expression, ''))
                        OR TRIM(COALESCE(delight_hook, '')) !=
                            TRIM(COALESCE(pool_topic_label, ''))
                      )
                    )
                  )
                  {guard_sql}
                  {_delight_unseen_guard_sql()}
                ORDER BY
                    relevance_score DESC,
                    delight_score DESC,
                    discovered_at DESC
                """,
                (
                    effective_min_relevance_score,
                    _DELIGHT_SCORE_SYNC_EPSILON,
                    min_delight_score_for_reason,
                    min_delight_score_for_reason,
                    *guard_params,
                ),
            )
        temporal_now = datetime.now(UTC)
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if self._temporal_eligibility_for_row(dict(row), now=temporal_now)
        ]
        return rows[:max_rows]

    @staticmethod
    def _decode_event_metadata(row: dict[str, Any]) -> dict[str, Any]:
        metadata_raw = row.get("metadata", "")
        if isinstance(metadata_raw, str) and metadata_raw:
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError:
                metadata = {}
            if isinstance(metadata, dict):
                return metadata
        if isinstance(metadata_raw, dict):
            return metadata_raw
        return {}

    @classmethod
    def _extract_content_keys_from_view_event(cls, row: dict[str, Any]) -> set[str]:
        keys, _ = cls._extract_view_event_identities(row)
        return keys

    @classmethod
    def _extract_view_event_identities(
        cls,
        row: dict[str, Any],
    ) -> tuple[set[str], str]:
        """Extract source-aware keys and BVID without decoding metadata twice."""
        metadata = cls._decode_event_metadata(row)
        url = str(row.get("url", "")).strip()

        bvid = str(metadata.get("bvid", "")).strip()
        bvid_match = _BVID_PATTERN.search(url)
        if not bvid and bvid_match:
            bvid = bvid_match.group(1)
        platform = _normalize_source_platform_key(metadata.get("source_platform", ""))
        if not platform:
            # Nearly all Bilibili view events already carry a BVID. Avoid the
            # generic URL classifier (and its urlparse) when the same URL has
            # an explicit BVID; fall back for cross-platform or malformed rows.
            platform = (
                _BILIBILI_SOURCE_FAMILY
                if bvid and (not url or bvid_match is not None)
                else cls._infer_source_platform_from_url(url)
            )

        content_id = extract_source_content_id(metadata)
        if not content_id:
            content_id = cls._extract_content_id_from_url(platform, url)
        if not content_id:
            content_id = bvid
            platform = platform or _BILIBILI_SOURCE_FAMILY

        keys: set[str] = set()
        if content_id:
            content_id_aliases: tuple[str, ...] = (content_id,)
            if (
                platform == _REDDIT_SOURCE_FAMILY
                and not content_id.startswith("t3_")
                and re.fullmatch(r"[A-Za-z0-9_]+", content_id)
            ):
                content_id_aliases = (content_id, f"t3_{content_id}")
            for content_id_alias in content_id_aliases:
                if content_id_alias.startswith("BV"):
                    keys.add(content_id_alias)
                if platform:
                    keys.add(f"{platform}:{content_id_alias}")
        return keys, bvid

    @staticmethod
    def _infer_source_platform_from_url(url: str) -> str:
        return infer_source_platform_from_url(url)

    @staticmethod
    def _extract_content_id_from_url(platform: str, url: str) -> str:
        if not url:
            return ""
        if platform == _BILIBILI_SOURCE_FAMILY:
            match = _BVID_PATTERN.search(url)
            return match.group(1) if match else ""
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if platform == _XHS_SOURCE_FAMILY:
            if len(path_parts) >= 2 and path_parts[0] == "explore":
                return path_parts[1]
            if len(path_parts) >= 3 and path_parts[:2] == ["discovery", "item"]:
                return path_parts[2]
        if platform == _DOUYIN_SOURCE_FAMILY and "video" in path_parts:
            video_index = path_parts.index("video")
            if len(path_parts) > video_index + 1:
                return path_parts[video_index + 1]
        if platform == _YOUTUBE_SOURCE_FAMILY:
            query_video_id = parse_qs(parsed.query).get("v", [""])[0].strip()
            if query_video_id:
                return query_video_id
            if parsed.netloc.lower() == "youtu.be" and path_parts:
                return path_parts[0]
            for prefix in ("shorts", "embed", "live"):
                if prefix in path_parts:
                    prefix_index = path_parts.index(prefix)
                    if len(path_parts) > prefix_index + 1:
                        return path_parts[prefix_index + 1]
        if platform == _REDDIT_SOURCE_FAMILY:
            host = parsed.netloc.lower()
            if host == "redd.it" and path_parts:
                return f"t3_{path_parts[0]}"
            if len(path_parts) >= 4 and path_parts[0] == "r" and path_parts[2] == "comments":
                return f"t3_{path_parts[3]}"
        if (
            platform == _BANGUMI_SOURCE_FAMILY
            and len(path_parts) >= 2
            and path_parts[0] == "subject"
        ):
            return path_parts[1] if path_parts[1].isdigit() else ""
        return ""

    @staticmethod
    def _extract_bvid_from_view_event(row: dict[str, Any]) -> str:
        metadata = Database._decode_event_metadata(row)
        bvid = str(metadata.get("bvid", "")).strip()
        if bvid:
            return bvid

        url = str(row.get("url", "")).strip()
        match = _BVID_PATTERN.search(url)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _content_row_view_keys(row: dict[str, Any]) -> set[str]:
        platform = _normalize_source_platform_key(row.get("source_platform", ""))
        if not platform:
            platform = _pool_source_family(row.get("source", ""), row.get("source_platform", ""))
            if platform == "unknown":
                platform = ""

        keys: set[str] = set()
        raw_bvid = str(row.get("bvid", "") or "").strip()
        content_id = str(row.get("content_id", "") or "").strip() or raw_bvid
        for value in {raw_bvid, content_id}:
            if not value:
                continue
            if value.startswith("BV"):
                keys.add(value)
            if platform:
                keys.add(f"{platform}:{value}")
        return keys

    @staticmethod
    def _is_viewed_row(row: dict[str, Any], viewed_content_keys: set[str]) -> bool:
        if not viewed_content_keys:
            return False
        return bool(Database._content_row_view_keys(row) & viewed_content_keys)

    @staticmethod
    def _exclude_viewed_rows(
        rows: list[dict[str, Any]],
        viewed_content_keys: set[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not viewed_content_keys:
            return rows[:limit]
        filtered = [row for row in rows if not Database._is_viewed_row(row, viewed_content_keys)]
        return filtered[:limit]
