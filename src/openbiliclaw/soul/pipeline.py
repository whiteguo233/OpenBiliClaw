"""Profile Update Pipeline — single entry point for profile-affecting signals.

Ordinary behavioral, dialogue, and account-sync signals flow through
``ingest()`` / ``ingest_batch()``. Durable content-feedback events use the
buffer-only ``enqueue()`` boundary first, then their sole cursor owner calls
``tick()``. Both paths share classification, buffering, persistence, and the
same serialized per-layer consumption machinery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.avoidance_speculator import AvoidanceSpeculator
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
    from openbiliclaw.soul.profile_builder import ProfileBuilder
    from openbiliclaw.soul.speculator import InterestSpeculator

from openbiliclaw.soul.dislike_writeback import (
    apply_new_dislikes,
    topics_for_confirmed_avoidance,
)
from openbiliclaw.sources.event_format import NON_PROFILE_EVENT_TYPES

logger = logging.getLogger(__name__)


def _coerce_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _coerce_float(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SignalType(Enum):
    """Discriminator for signal payloads."""

    BEHAVIOR_EVENT = "behavior_event"
    ENGAGEMENT_EVENT = "engagement_event"
    FEEDBACK = "feedback"
    DIALOGUE_INSIGHT = "dialogue_insight"
    DIALOGUE_TURN = "dialogue_turn"
    ACCOUNT_SNAPSHOT = "account_snapshot"
    # Explicit click-through on a recommendation card in the extension popup.
    # The user trusted the recommender enough to open the video — this is a
    # strong positive signal that reveals both interest and taste.
    RECOMMENDATION_CLICK = "recommendation_click"


class OnionLayer(Enum):
    """The five onion layers plus the cross-layer synthesis."""

    SURFACE = "surface"
    INTEREST = "interest"
    ROLE = "role"
    VALUES = "values"
    CORE = "core"
    PORTRAIT = "portrait"


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

# Engagement event types that indicate strong interest signals
_ENGAGEMENT_TYPES = frozenset({"like", "coin", "favorite", "comment"})


@dataclass(frozen=True)
class ProfileSignal:
    """A single piece of evidence that may affect the user profile."""

    id: str
    signal_type: SignalType
    timestamp: str
    source: str
    payload: dict[str, object]
    target_layers: frozenset[OnionLayer]
    confidence: float = 0.0


def _is_non_profile_signal(signal: ProfileSignal) -> bool:
    """Whether a signal comes from a context-only collector event type.

    These rows stay in the durable event ledger and their consumer cursor still
    advances past them; they are simply never buffered as profile evidence or
    handed to the speculators. ``search`` is intentionally absent from
    ``NON_PROFILE_EVENT_TYPES`` (it is a real intent signal).
    """
    payload = signal.payload
    if not isinstance(payload, dict):
        return False
    event_type = str(payload.get("event_type") or payload.get("type") or "").strip().lower()
    return event_type in NON_PROFILE_EVENT_TYPES


# ---------------------------------------------------------------------------
# Layer buffer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayerThreshold:
    """Per-layer gating configuration."""

    min_signals: int
    min_interval_seconds: int
    max_buffer_size: int


@dataclass
class LayerBuffer:
    """Per-layer signal accumulator."""

    layer: OnionLayer
    signals: list[dict[str, object]] = field(default_factory=list)
    last_updated_at: str = ""
    update_count: int = 0

    def is_ready(
        self,
        threshold: LayerThreshold,
        now: datetime,
        *,
        has_strong_signal: bool = False,
        feedback_priority_threshold: int = 0,
    ) -> bool:
        """Check if this buffer has enough signals and enough time has passed.

        If *has_strong_signal* is True the min_signals gate is reduced to 1,
        so feedback and dialogue signals update the profile immediately.

        *feedback_priority_threshold* > 0 enables the unified-interest-line
        priority rule (spec 2026-07-27 invariant 1): once the buffer holds that
        many FEEDBACK signals the update fires immediately, bypassing
        ``min_interval_seconds``. The threshold is the old feedback batch's
        ``feedback_batch_threshold`` (default 3, calibrated 2026-03-09 by
        ``fcbde4a2``; the value is carried over unchanged, only its trigger site
        moved). 0 (the default, and what the flag-off path passes) keeps today's
        behaviour byte-identical.
        """
        effective_min = 1 if has_strong_signal else threshold.min_signals
        if len(self.signals) < effective_min:
            return False
        if feedback_priority_threshold > 0:
            feedback_signals = sum(
                1 for s in self.signals if s.get("signal_type") == SignalType.FEEDBACK.value
            )
            if feedback_signals >= feedback_priority_threshold:
                return True
        if self.last_updated_at:
            try:
                last = datetime.fromisoformat(self.last_updated_at)
                elapsed = (now - last).total_seconds()
                if elapsed < threshold.min_interval_seconds:
                    return False
            except ValueError:
                pass
        return True

    def evict(self, max_size: int) -> None:
        """Drop oldest signals if buffer exceeds max size."""
        if len(self.signals) > max_size:
            self.signals = self.signals[-max_size:]

    def drain(self) -> list[dict[str, object]]:
        """Remove and return all buffered signals."""
        signals = list(self.signals)
        self.signals = []
        return signals

    def to_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "signals": self.signals,
            "last_updated_at": self.last_updated_at,
            "update_count": self.update_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LayerBuffer:
        layer_str = str(data.get("layer", "surface"))
        try:
            layer = OnionLayer(layer_str)
        except ValueError:
            layer = OnionLayer.SURFACE
        raw_signals = data.get("signals")
        signals = [
            s for s in (raw_signals if isinstance(raw_signals, list) else []) if isinstance(s, dict)
        ]
        return cls(
            layer=layer,
            signals=signals,
            last_updated_at=str(data.get("last_updated_at", "")),
            update_count=_coerce_int(data.get("update_count", 0) or 0),
        )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class LayerUpdateResult:
    """Result of a single layer update cycle."""

    layer: OnionLayer
    changed: bool
    changes: list[str] = field(default_factory=list)
    signals_consumed: int = 0
    trigger: str = ""
    evidence: str = ""
    timestamp: str = ""
    # Set by the INTEREST updater when the consumed batch carried FEEDBACK
    # signals (unified interest line). The pipeline runs the feedback-batch
    # post-write privileges (gated soul rebuild + held replay) from it AFTER
    # the profile save, so a rebuild is never clobbered by the layer write.
    feedback_context: dict[str, object] | None = None


@dataclass(frozen=True)
class FeedbackConsumerHooks:
    """Feedback-batch privileges the unified interest line inherits.

    Wired by the SoulEngine only when ``scheduler.unified_interest_line`` is on;
    ``None`` hooks mean the pipeline behaves exactly as it does today.

    - ``archive_dislikes(updated_preference, newly_added)`` runs BEFORE the
      preference write so newly disliked topics are archived (not deleted) in
      the same snapshot the profile is populated from.
    - ``after_update(...)`` runs AFTER the profile write: gated soul rebuild
      (access point ③, trigger ``feedback_batch``) plus the held-replay
      consumer.
    """

    archive_dislikes: Callable[[dict[str, Any], list[str]], None]
    after_update: Callable[..., Awaitable[None]]


@dataclass
class IngestResult:
    """Result of ingesting one or more signals."""

    signals_accepted: int = 0
    layers_buffered: list[str] = field(default_factory=list)
    layers_updated: list[LayerUpdateResult] = field(default_factory=list)


@dataclass
class FlushResult:
    """Result of flushing (force-updating) layers."""

    layers_updated: list[LayerUpdateResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------

_STATIC_LAYER_MAP: dict[SignalType, frozenset[OnionLayer]] = {
    SignalType.BEHAVIOR_EVENT: frozenset(
        {
            OnionLayer.SURFACE,
            OnionLayer.INTEREST,
            OnionLayer.ROLE,
        }
    ),
    SignalType.ENGAGEMENT_EVENT: frozenset(
        {
            OnionLayer.INTEREST,
            OnionLayer.SURFACE,
            OnionLayer.ROLE,
        }
    ),
    # FEEDBACK no longer routes to VALUES (deep-line consolidation, P1 retired):
    # deep profile change is driven exclusively by gated soul rebuild (access
    # point ③) fed from validated hypotheses / feedback batches, never by a
    # direct pipeline VALUES write.
    SignalType.FEEDBACK: frozenset(
        {
            OnionLayer.INTEREST,
            OnionLayer.SURFACE,
        }
    ),
    SignalType.DIALOGUE_TURN: frozenset({OnionLayer.SURFACE, OnionLayer.INTEREST}),
    SignalType.ACCOUNT_SNAPSHOT: frozenset(
        {
            OnionLayer.INTEREST,
            OnionLayer.SURFACE,
            OnionLayer.ROLE,
        }
    ),
    # Click-through reveals immediate topical preference (INTEREST) and
    # content-style preference (SURFACE). It does not touch ROLE/VALUES —
    # a single click is not strong enough evidence about life stage or values.
    SignalType.RECOMMENDATION_CLICK: frozenset(
        {
            OnionLayer.INTEREST,
            OnionLayer.SURFACE,
        }
    ),
    SignalType.DIALOGUE_INSIGHT: frozenset(),  # Dynamic, see classify_signal
}

# Dialogue insight kind → target layers.
# ``value`` / ``state`` are inert in the pipeline (empty target set) after the
# deep-line consolidation (P1 retired): a deep self-report reaches the profile
# only through the gated dialogue-deep-candidate path (access point ①) in the
# SoulEngine, never through a pipeline VALUES/CORE buffer write.
_DIALOGUE_INSIGHT_KIND_MAP: dict[str, frozenset[OnionLayer]] = {
    "interest": frozenset({OnionLayer.INTEREST}),
    "dislike": frozenset({OnionLayer.INTEREST}),
    "value": frozenset(),
    "goal": frozenset({OnionLayer.ROLE}),
    "state": frozenset(),
}


def classify_signal(signal_type: SignalType, payload: dict[str, object]) -> frozenset[OnionLayer]:
    """Determine which onion layers a signal can affect."""
    if signal_type == SignalType.DIALOGUE_INSIGHT:
        kind = str(payload.get("kind", ""))
        return _DIALOGUE_INSIGHT_KIND_MAP.get(kind, frozenset({OnionLayer.INTEREST}))
    return _STATIC_LAYER_MAP.get(signal_type, frozenset())


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: dict[OnionLayer, LayerThreshold] = {
    OnionLayer.SURFACE: LayerThreshold(
        min_signals=3,
        min_interval_seconds=300,
        max_buffer_size=200,
    ),
    OnionLayer.INTEREST: LayerThreshold(
        min_signals=3,
        min_interval_seconds=600,
        max_buffer_size=200,
    ),
    OnionLayer.ROLE: LayerThreshold(
        min_signals=5,
        min_interval_seconds=86400,
        max_buffer_size=50,
    ),
    OnionLayer.VALUES: LayerThreshold(
        min_signals=5,
        min_interval_seconds=86400,
        max_buffer_size=50,
    ),
    OnionLayer.CORE: LayerThreshold(
        min_signals=8,
        min_interval_seconds=172800,
        max_buffer_size=30,
    ),
}

# Layers that trigger portrait regeneration when changed. VALUES/CORE are
# retired from the pipeline (deep-line consolidation) so this only ever fires
# from the (now removed) deep path; kept as a harmless no-op guard.
_PORTRAIT_TRIGGER_LAYERS = frozenset({OnionLayer.CORE, OnionLayer.VALUES})

# Layers that participate in buffering (PORTRAIT is conditional, not buffered).
# VALUES/CORE are intentionally EXCLUDED: the pipeline no longer consumes deep
# layers (P1 retired). A signal targeting VALUES/CORE is simply not buffered,
# and ``update_layer`` seals those two layers with a defensive no-op + WARNING.
_BUFFERED_LAYERS = frozenset(
    {
        OnionLayer.SURFACE,
        OnionLayer.INTEREST,
        OnionLayer.ROLE,
    }
)

# Signal types that carry explicit user intent.
# For these, the min_signals gate is reduced to 1 so the profile updates immediately.
_STRONG_SIGNAL_TYPES: frozenset[SignalType] = frozenset(
    {
        SignalType.FEEDBACK,
        SignalType.DIALOGUE_TURN,
        SignalType.DIALOGUE_INSIGHT,
        SignalType.RECOMMENDATION_CLICK,
    }
)
_STRONG_TYPE_VALUES: frozenset[str] = frozenset(st.value for st in _STRONG_SIGNAL_TYPES)

# Only direct user reactions to a piece of recommended/browser content belong
# to the durable feedback cursor. ``event_type=feedback`` is also used by
# hypothesis settlement and imported source snapshots (currently Bangumi),
# which already have their own learning owner and must not be promoted into a
# second FEEDBACK strong signal.
CONTENT_FEEDBACK_TYPES: frozenset[str] = frozenset({"like", "dislike", "comment", "dismiss"})


# ---------------------------------------------------------------------------
# Signal factory helpers
# ---------------------------------------------------------------------------


def feedback_type_from_event(event: dict[str, Any]) -> str:
    """Return normalized ``metadata.feedback_type`` from a raw or DB event."""
    metadata = event.get("metadata")
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except json.JSONDecodeError:
            parsed = {}
        metadata = parsed if isinstance(parsed, dict) else {}
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("feedback_type") or "").strip().lower()


def is_content_feedback_event(
    event: dict[str, Any],
    *,
    legacy_cutover_event_id: int | None = None,
) -> bool:
    """Whether the durable feedback cursor owns this explicit content reaction.

    Imported snapshots are excluded even when they encode a low score as
    ``feedback_type=dislike``: guided init already analyzes those rows in its
    source batch, so cursor replay would double-learn them.
    """
    event_type = str(event.get("event_type") or event.get("type") or "").strip()
    if event_type != "feedback" or feedback_type_from_event(event) not in CONTENT_FEEDBACK_TYPES:
        return False
    metadata = event.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    # Explicit ownership is authoritative for every newly-written row.  The
    # legacy predicate below exists only for rows already present when owner-v2
    # cut over; otherwise a future producer that forgot the owner flag could be
    # silently double-consumed by this lane.
    owner = str(metadata.get("profile_update_owner") or "").strip().lower()
    if owner:
        return owner == "content_feedback"
    namespace = str(metadata.get("event_namespace") or "").strip().lower()
    if namespace in {"hypothesis", "probe", "import", "retraction"}:
        return False
    if str(metadata.get("import_source") or "").strip():
        return False

    if legacy_cutover_event_id is not None:
        boundary = max(0, int(legacy_cutover_event_id))
        try:
            event_id = int(event.get("id", 0) or 0)
        except (TypeError, ValueError):
            event_id = 0
        return 0 < event_id <= boundary
    return True


def _make_signal(
    signal_type: SignalType,
    source: str,
    payload: dict[str, object],
    confidence: float = 0.0,
) -> ProfileSignal:
    """Create a ProfileSignal with auto-generated id, timestamp, and classification."""
    return ProfileSignal(
        id=uuid4().hex[:12],
        signal_type=signal_type,
        timestamp=datetime.now().isoformat(),
        source=source,
        payload=payload,
        target_layers=classify_signal(signal_type, payload),
        confidence=confidence,
    )


def signals_from_events(events: list[dict[str, Any]]) -> list[ProfileSignal]:
    """Convert raw behavioral events into ProfileSignals."""
    return [signal_from_event(event) for event in events]


def signal_from_event(
    event: dict[str, Any],
    *,
    signal_id: str = "",
) -> ProfileSignal:
    """Convert one event, optionally using a durable row-derived signal id."""
    event_type = str(event.get("event_type") or event.get("type") or "")
    feedback_type = feedback_type_from_event(event)
    if event_type == "feedback" and feedback_type == "retraction":
        # A retraction is a neutralization, never a strong FEEDBACK signal.
        signal_type = SignalType.BEHAVIOR_EVENT
    elif event_type in _ENGAGEMENT_TYPES:
        signal_type = SignalType.ENGAGEMENT_EVENT
    else:
        signal_type = SignalType.BEHAVIOR_EVENT
    signal = _make_signal(signal_type, "events", dict(event))
    if not signal_id:
        return signal
    return ProfileSignal(
        id=signal_id,
        signal_type=signal.signal_type,
        timestamp=signal.timestamp,
        source=signal.source,
        payload=signal.payload,
        target_layers=signal.target_layers,
        confidence=signal.confidence,
    )


def signal_from_feedback(
    feedback_type: str,
    title: str,
    note: str = "",
    *,
    signal_id: str = "",
) -> ProfileSignal:
    """Convert a recommendation feedback action into a ProfileSignal."""
    signal = _make_signal(
        SignalType.FEEDBACK,
        "feedback",
        {"feedback_type": feedback_type, "title": title, "note": note},
    )
    if not signal_id:
        return signal
    return ProfileSignal(
        id=signal_id,
        signal_type=signal.signal_type,
        timestamp=signal.timestamp,
        source=signal.source,
        payload=signal.payload,
        target_layers=signal.target_layers,
        confidence=signal.confidence,
    )


def signals_from_dialogue(
    candidates: list[dict[str, object]],
) -> list[ProfileSignal]:
    """Convert dialogue-derived insight candidates into ProfileSignals.

    Only candidates that have reached the readiness threshold
    (confidence >= 0.8 or occurrences >= 2) should be passed here.
    """
    result: list[ProfileSignal] = []
    for candidate in candidates:
        confidence = _coerce_float(candidate.get("confidence", 0.0) or 0.0)
        result.append(
            _make_signal(
                SignalType.DIALOGUE_INSIGHT,
                "dialogue",
                dict(candidate),
                confidence=confidence,
            )
        )
    return result


def signal_from_dialogue_turn(
    user_message: str,
    assistant_reply: str,
) -> ProfileSignal:
    """Convert a raw dialogue turn into a Surface-layer signal."""
    return _make_signal(
        SignalType.DIALOGUE_TURN,
        "dialogue",
        {"user_message": user_message, "assistant_reply": assistant_reply},
    )


def signals_from_account_sync(events: list[dict[str, Any]]) -> list[ProfileSignal]:
    """Convert account sync events into ProfileSignals."""
    result: list[ProfileSignal] = []
    for event in events:
        result.append(_make_signal(SignalType.ACCOUNT_SNAPSHOT, "account_sync", dict(event)))
    return result


def signal_from_recommendation_click(
    bvid: str,
    title: str = "",
    *,
    recommendation_id: int | None = None,
    topic_label: str = "",
    up_name: str = "",
    content_id: str = "",
    content_url: str = "",
    source_platform: str = "",
    signal_id: str = "",
) -> ProfileSignal:
    """Convert a recommendation click-through into a strong profile signal.

    The user actively chose to open this video from a recommendation — that
    is a high-signal positive vote for both topic (interest) and presentation
    style (surface). This signal bypasses the min_signals gate so the profile
    updates immediately.
    """
    payload: dict[str, object] = {
        "bvid": bvid,
        "title": title,
        "event_type": "recommendation_click",
    }
    if recommendation_id is not None:
        payload["recommendation_id"] = recommendation_id
    if topic_label:
        payload["topic_label"] = topic_label
    if up_name:
        payload["up_name"] = up_name
    if content_id:
        payload["content_id"] = content_id
    if content_url:
        payload["content_url"] = content_url
    if source_platform:
        payload["source_platform"] = source_platform
    signal = _make_signal(SignalType.RECOMMENDATION_CLICK, "recommendation", payload)
    if not signal_id:
        return signal
    return ProfileSignal(
        id=signal_id,
        signal_type=signal.signal_type,
        timestamp=signal.timestamp,
        source=signal.source,
        payload=signal.payload,
        target_layers=signal.target_layers,
        confidence=signal.confidence,
    )


# ---------------------------------------------------------------------------
# Pipeline state persistence
# ---------------------------------------------------------------------------


def _serialize_signal(signal: ProfileSignal) -> dict[str, object]:
    """Convert a ProfileSignal to a JSON-serializable dict for buffer storage."""
    return {
        "id": signal.id,
        "signal_type": signal.signal_type.value,
        "timestamp": signal.timestamp,
        "source": signal.source,
        "payload": signal.payload,
        "confidence": signal.confidence,
    }


@dataclass(frozen=True)
class PipelinePersistedState:
    """Backward-compatible buffers plus durable consumer ownership metadata."""

    buffers: dict[str, LayerBuffer]
    consumer_cursors: dict[str, int] = field(default_factory=dict)
    consumer_owner_versions: dict[str, int] = field(default_factory=dict)
    consumer_cutover_at: dict[str, str] = field(default_factory=dict)
    consumer_cutover_event_ids: dict[str, int] = field(default_factory=dict)
    total_signals_ingested: int = 0


def load_pipeline_snapshot(data_dir: Path) -> PipelinePersistedState:
    """Load buffers and additive consumer checkpoints from one JSON snapshot."""
    state_path = data_dir / "memory" / "pipeline_state.json"
    buffers: dict[str, LayerBuffer] = {}
    for layer in _BUFFERED_LAYERS:
        buffers[layer.value] = LayerBuffer(layer=layer)

    if not state_path.exists():
        return PipelinePersistedState(buffers=buffers)

    try:
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return PipelinePersistedState(buffers=buffers)

    raw_buffers = data.get("buffers")
    if not isinstance(raw_buffers, dict):
        raw_buffers = {}

    for key, raw_buf in raw_buffers.items():
        if isinstance(raw_buf, dict) and key in buffers:
            buffers[key] = LayerBuffer.from_dict(raw_buf)

    def _int_map(value: object) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key): max(0, _coerce_int(item)) for key, item in value.items() if str(key).strip()
        }

    def _str_map(value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item) for key, item in value.items() if str(key).strip()}

    return PipelinePersistedState(
        buffers=buffers,
        consumer_cursors=_int_map(data.get("consumer_cursors")),
        consumer_owner_versions=_int_map(data.get("consumer_owner_versions")),
        consumer_cutover_at=_str_map(data.get("consumer_cutover_at")),
        consumer_cutover_event_ids=_int_map(data.get("consumer_cutover_event_ids")),
        total_signals_ingested=max(0, _coerce_int(data.get("total_signals_ingested", 0))),
    )


def load_pipeline_state(data_dir: Path) -> dict[str, LayerBuffer]:
    """Load pipeline buffers while tolerating pre-checkpoint state files."""
    return load_pipeline_snapshot(data_dir).buffers


def save_pipeline_state(
    data_dir: Path,
    buffers: dict[str, LayerBuffer],
    total_ingested: int = 0,
    *,
    consumer_cursors: dict[str, int] | None = None,
    consumer_owner_versions: dict[str, int] | None = None,
    consumer_cutover_at: dict[str, str] | None = None,
    consumer_cutover_event_ids: dict[str, int] | None = None,
) -> None:
    """Atomically persist buffers and consumer checkpoints in one replace."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    state_path = memory_dir / "pipeline_state.json"

    existing: dict[str, object] = {}
    if state_path.exists():
        try:
            with open(state_path, encoding="utf-8") as file:
                loaded = json.load(file)
            if isinstance(loaded, dict):
                existing = dict(loaded)
        except (OSError, ValueError):
            existing = {}
    existing_cursors = existing.get("consumer_cursors")
    if not isinstance(existing_cursors, dict):
        existing_cursors = {}
    existing_owner_versions = existing.get("consumer_owner_versions")
    if not isinstance(existing_owner_versions, dict):
        existing_owner_versions = {}
    existing_cutover_at = existing.get("consumer_cutover_at")
    if not isinstance(existing_cutover_at, dict):
        existing_cutover_at = {}
    existing_cutover_event_ids = existing.get("consumer_cutover_event_ids")
    if not isinstance(existing_cutover_event_ids, dict):
        existing_cutover_event_ids = {}
    payload = {
        **existing,
        "version": 2,
        "buffers": {key: buf.to_dict() for key, buf in buffers.items()},
        "last_saved_at": datetime.now().isoformat(),
        "total_signals_ingested": total_ingested,
        "consumer_cursors": (
            dict(existing_cursors) if consumer_cursors is None else dict(consumer_cursors or {})
        ),
        "consumer_owner_versions": (
            dict(existing_owner_versions)
            if consumer_owner_versions is None
            else dict(consumer_owner_versions or {})
        ),
        "consumer_cutover_at": (
            dict(existing_cutover_at)
            if consumer_cutover_at is None
            else dict(consumer_cutover_at or {})
        ),
        "consumer_cutover_event_ids": (
            dict(existing_cutover_event_ids)
            if consumer_cutover_event_ids is None
            else dict(consumer_cutover_event_ids or {})
        ),
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        dir=memory_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_name, state_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


# ---------------------------------------------------------------------------
# P1 retirement — one-time deep-buffer migration (deep-line consolidation)
# ---------------------------------------------------------------------------

# Provenance prefix stamped onto every awareness note synthesised from a retired
# VALUES/CORE pipeline buffer signal. ``AwarenessNote`` has no ``source`` field,
# so provenance lives in the observation text — a deterministic, no-LLM stamp.
_PIPELINE_DEEP_MIGRATION_PREFIX = "[migration:pipeline-deep]"
# Idempotency marker persisted in pipeline_state.json. Once set, the migration
# short-circuits. A crash between the note write and the marker write is still
# safe: content-hash dedup makes a re-run add nothing new, and the deep buffer
# keys are cleared in the same write as the marker.
_PIPELINE_DEEP_MIGRATION_MARKER = "pipeline_deep_migrated"
# The retired deep-layer persisted buffer keys, read VERBATIM (independent of
# ``_BUFFERED_LAYERS`` which no longer lists them — spec r3/F5).
_DEEP_MIGRATION_LAYER_KEYS = ("values", "core")


def _normalize_migration_text(text: str) -> str:
    """Cheap deterministic normaliser for content-hash dedup of migrated notes."""
    return " ".join(str(text).split()).strip().lower()


def _deep_signal_observation(layer_key: str, signal: dict[str, object]) -> str:
    """Render a retired deep-buffer signal as an awareness observation (no LLM).

    Pulls the human-readable fields the retired VALUES/CORE updaters used as
    evidence (title / event_type / content) and stamps provenance so the note
    is traceable back to the pipeline-deep migration.
    """
    raw_payload = signal.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    title = str(payload.get("title", "")).strip()
    event_type = str(payload.get("event_type", "")).strip()
    content = str(payload.get("content", "")).strip()
    if title:
        body = f"[{event_type}] {title}" if event_type else title
    elif content:
        body = content
    else:
        body = str(signal.get("id", "")).strip() or "(无文本证据)"
    return f"{_PIPELINE_DEEP_MIGRATION_PREFIX} {layer_key}: {body}"


def _deep_signal_source_event_ids(signal: dict[str, object]) -> list[int]:
    """Best-effort event-id backfill for a migrated note (empty when absent)."""
    raw_payload = signal.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw = payload.get("id", payload.get("event_id"))
    if isinstance(raw, bool):
        return []
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str) and raw.strip().isdigit():
        return [int(raw.strip())]
    return []


def migrate_pipeline_deep_buffers(
    data_dir: Path,
    memory: MemoryManager,
    ledger: Any | None = None,
) -> int:
    """One-time migration of retired VALUES/CORE buffer signals to awareness.

    Reads the persisted ``pipeline_state.json`` deep-buffer keys verbatim,
    converts each signal deterministically into an awareness note (prefix
    ``[migration:pipeline-deep]``, content-hash dedup), then — in a single
    persistent write — clears the deep buffer keys and sets an idempotency
    marker. Records one best-effort ledger row.

    Returns the number of NEW awareness notes written (0 if already migrated or
    nothing to migrate). Idempotent: the marker short-circuits re-runs, and a
    crash before the marker write is safe (dedup + cleared keys).
    """
    from openbiliclaw.soul.profile import (
        AwarenessNote,
        awareness_note_from_dict,
        awareness_note_to_dict,
    )

    state_path = data_dir / "memory" / "pipeline_state.json"
    if not state_path.exists():
        return 0
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(state, dict) or state.get(_PIPELINE_DEEP_MIGRATION_MARKER):
        return 0
    raw_buffers = state.get("buffers")
    if not isinstance(raw_buffers, dict):
        raw_buffers = {}

    deep_signals: list[tuple[str, dict[str, object]]] = []
    for key in _DEEP_MIGRATION_LAYER_KEYS:
        buf = raw_buffers.get(key)
        if not isinstance(buf, dict):
            continue
        raw_signals = buf.get("signals")
        for sig in raw_signals if isinstance(raw_signals, list) else []:
            if isinstance(sig, dict):
                deep_signals.append((key, sig))

    added = 0
    if deep_signals:
        awareness_layer = memory.get_layer("awareness")
        raw_notes = awareness_layer.data.get("notes", [])
        existing_notes = [awareness_note_from_dict(n) for n in raw_notes if isinstance(n, dict)]
        seen = {_normalize_migration_text(n.observation) for n in existing_notes}
        new_notes: list[AwarenessNote] = []
        for layer_key, sig in deep_signals:
            observation = _deep_signal_observation(layer_key, sig)
            norm = _normalize_migration_text(observation)
            if norm in seen:
                continue
            seen.add(norm)
            new_notes.append(
                AwarenessNote(
                    date=str(sig.get("timestamp", ""))[:10],
                    observation=observation,
                    note_id=uuid4().hex[:12],
                    source_event_ids=_deep_signal_source_event_ids(sig),
                    source_event_ids_approximate=False,
                )
            )
        if new_notes:
            # Step ①: durable note write (content-hash dedup ⇒ crash-safe re-run).
            merged = existing_notes + new_notes
            awareness_layer.data.clear()
            awareness_layer.data["notes"] = [awareness_note_to_dict(n) for n in merged]
            awareness_layer.save()
            added = len(new_notes)

    # Step ②: single persistent write — clear deep keys + set idempotency marker.
    for key in _DEEP_MIGRATION_LAYER_KEYS:
        buf = raw_buffers.get(key)
        if isinstance(buf, dict):
            buf["signals"] = []
    state["buffers"] = raw_buffers
    state[_PIPELINE_DEEP_MIGRATION_MARKER] = True
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        logger.warning("pipeline deep migration: failed to persist marker/clear", exc_info=True)

    if ledger is not None and deep_signals:
        try:
            ledger.record(
                write_point="pipeline_deep_migration",
                source="pipeline_migration",
                before={"deep_signal_count": len(deep_signals)},
                after={"awareness_notes_added": added},
                source_refs=sorted({key for key, _ in deep_signals}) or ["pipeline_deep"],
                outcome="success",
            )
        except Exception:  # pragma: no cover - ledger is best-effort
            logger.debug("pipeline deep migration ledger record failed", exc_info=True)

    return added


# ---------------------------------------------------------------------------
# Retraction discounting (event-capture-completion Phase 0, face 1/1b)
# ---------------------------------------------------------------------------

# Out-of-order tombstone bounds. Calibration:
# - TTL 24h covers the extension buffer's resend window plus the account_sync
#   dual cycle, so a positive that arrives late but within a day is still
#   discounted by an earlier retraction.
# - Cap 500 far exceeds a single user's real retraction frequency; on overflow
#   the oldest tombstones are evicted first.
_RETRACTION_TOMBSTONE_TTL = timedelta(hours=24)
_RETRACTION_TOMBSTONE_CAP = 500


def _payload_event_type(payload: dict[str, object]) -> str:
    return str(payload.get("event_type") or payload.get("type") or "").strip().lower()


def _positive_signal_descriptor(
    payload: dict[str, object],
) -> tuple[str, str, datetime | None] | None:
    """Return ``(identity_key, action, event_time)`` for a retractable positive.

    ``None`` when the payload is not a whitelisted positive, has no identity
    key, or lacks dict metadata.
    """
    from openbiliclaw.sources.event_format import RETRACTABLE_ACTIONS, parse_event_timestamp
    from openbiliclaw.sources.identity_keys import dedup_key

    action = _payload_event_type(payload)
    if action not in RETRACTABLE_ACTIONS:
        return None
    key = dedup_key(str(payload.get("url") or ""))
    if not key:
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return key, action, parse_event_timestamp(metadata)


def _retraction_signal_descriptor(
    payload: dict[str, object],
) -> tuple[str, str, datetime | None] | None:
    """Return ``(identity_key, action, event_time)`` for a retraction feedback.

    Logs + skips (returns ``None``) when ``retracted_action`` is out of the
    whitelist (invariant 4) or no identity key applies.
    """
    from openbiliclaw.sources.event_format import RETRACTABLE_ACTIONS, parse_event_timestamp
    from openbiliclaw.sources.identity_keys import dedup_key

    metadata = payload.get("metadata")
    if _payload_event_type(payload) != "feedback" or not isinstance(metadata, dict):
        return None
    if str(metadata.get("feedback_type") or "").strip().lower() != "retraction":
        return None
    action = str(metadata.get("retracted_action") or "").strip().lower()
    if action not in RETRACTABLE_ACTIONS:
        logger.warning("retraction discount: skipping out-of-whitelist retracted_action %r", action)
        return None
    key = dedup_key(str(payload.get("url") or ""))
    if not key:
        return None
    return key, action, parse_event_timestamp(metadata)


def _discount_payload(payload: dict[str, object]) -> None:
    """Fold a positive payload's metadata to retracted + capped strength."""
    from openbiliclaw.sources.event_format import apply_retraction_discount

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        payload["metadata"] = apply_retraction_discount(metadata)


# ---------------------------------------------------------------------------
# ProfileUpdatePipeline
# ---------------------------------------------------------------------------


class ProfileUpdatePipeline:
    """Consolidates all profile update signals into a single entry point.

    Usage:
        pipeline = ProfileUpdatePipeline(memory=..., preference_analyzer=..., ...)
        await pipeline.enqueue(signal)      # Durably buffer without analysis
        await pipeline.ingest(signal)       # Buffer and consume ready layers
        await pipeline.tick()               # Check and update ready layers
        await pipeline.flush()              # Force-update all layers (init)
    """

    def __init__(
        self,
        *,
        memory: MemoryManager,
        preference_analyzer: PreferenceAnalyzer,
        profile_builder: ProfileBuilder,
        thresholds: dict[OnionLayer, LayerThreshold] | None = None,
        speculator: InterestSpeculator | None = None,
        avoidance_speculator: AvoidanceSpeculator | None = None,
        embedding_service: Any | None = None,
        cognition_cycle: Any | None = None,
        speculator_idle_interval_minutes: int = 30,
        profile_consolidator: Any | None = None,
        unified_interest_line: bool = False,
        feedback_batch_threshold: int = 3,
    ) -> None:
        self._memory = memory
        self._preference_analyzer = preference_analyzer
        self._profile_builder = profile_builder
        self._thresholds = thresholds or dict(DEFAULT_THRESHOLDS)
        self._speculator = speculator
        self._avoidance_speculator = avoidance_speculator
        self._embedding_service = embedding_service
        self._cognition_cycle = cognition_cycle
        self._profile_consolidator = profile_consolidator
        data_dir = getattr(memory, "_data_dir", None)
        persisted = load_pipeline_snapshot(data_dir) if data_dir else None
        self._buffers = (
            persisted.buffers
            if persisted is not None
            else {layer.value: LayerBuffer(layer=layer) for layer in _BUFFERED_LAYERS}
        )
        # Serializes ingest/flush/tick layer updates so a burst of concurrent
        # callers (e.g. rapid feedback clicks + extension event batches) never
        # drains overlapping buffers and runs parallel LLM analyses, and so
        # pipeline_state.json snapshots never interleave.
        self._ingest_lock = asyncio.Lock()
        self._total_ingested = persisted.total_signals_ingested if persisted is not None else 0
        self._consumer_cursors = dict(persisted.consumer_cursors) if persisted is not None else {}
        self._consumer_owner_versions = (
            dict(persisted.consumer_owner_versions) if persisted is not None else {}
        )
        self._consumer_cutover_at = (
            dict(persisted.consumer_cutover_at) if persisted is not None else {}
        )
        self._consumer_cutover_event_ids = (
            dict(persisted.consumer_cutover_event_ids) if persisted is not None else {}
        )
        # Out-of-order retraction tombstones: (identity_key, action) → retraction
        # event time. In-memory only — the events table is the durable tombstone
        # for cross-restart / late-backfill reconciliation (face 2b).
        self._retraction_tombstones: dict[tuple[str, str], datetime] = {}
        # Track when we last ran the speculator tick so we can throttle
        # idle ticks while still letting layer-updates trigger fresh
        # speculator passes.  See `tick()` body for usage.
        self._last_speculator_tick_at: datetime | None = None
        # Minimum interval between speculator ticks when no layer was
        # updated.  Pipeline.tick itself runs every minute, but the
        # speculator only needs periodic expire/promote checks; idle
        # cadence at 30 minutes is plenty.
        self._speculator_idle_min_interval = timedelta(minutes=speculator_idle_interval_minutes)
        # Phase 3 posture gate over VALUES/CORE layer writes (access point ②).
        # None until the SoulEngine wires it in; None ⇒ no gating (byte-identical).
        self._posture_gate: Any | None = None
        # Unified interest line (spec 2026-07-27) feedback-priority rule. 0 ⇒ the
        # rule is off and every readiness check is byte-identical to today.
        self._feedback_priority_threshold = (
            max(1, feedback_batch_threshold) if unified_interest_line else 0
        )
        # Feedback-batch privileges for the consuming side. None until the
        # SoulEngine wires them in (only when the unified line is on).
        self._feedback_hooks: FeedbackConsumerHooks | None = None

    def set_embedding_service(self, embedding_service: Any) -> None:
        """Attach or replace the embedding service for semantic operations."""
        self._embedding_service = embedding_service

    def set_posture_gate(self, posture_gate: Any) -> None:
        """Attach the posture gate for VALUES/CORE deep-layer writes (Phase 3)."""
        self._posture_gate = posture_gate

    def set_feedback_hooks(self, hooks: FeedbackConsumerHooks | None) -> None:
        """Attach the feedback-batch privileges (unified interest line)."""
        self._feedback_hooks = hooks

    def set_cognition_cycle(self, cognition_cycle: Any) -> None:
        """Attach or replace the cognition cycle runner."""
        self._cognition_cycle = cognition_cycle

    # -- Public API -----------------------------------------------------------

    async def enqueue(self, signal: ProfileSignal) -> IngestResult:
        """Durably buffer one signal without running a layer update."""
        return await self.enqueue_batch([signal])

    async def enqueue_batch(self, signals: list[ProfileSignal]) -> IngestResult:
        """Durably buffer signals without entering the LLM-backed consume path.

        Signal IDs are idempotency keys while they remain in any persisted
        buffer. This lets a durable event cursor retry the narrow
        ``enqueue -> cursor`` crash window without adding the same evidence a
        second time.
        """
        async with self._ingest_lock:
            result, accepted = self._enqueue_batch_locked(signals)
            self._save_state()
            self._observe_signals(accepted)
            return result

    def consumer_checkpoint(self, consumer: str) -> dict[str, object]:
        """Return one durable consumer's cursor and ownership fence."""
        key = str(consumer).strip()
        return {
            "cursor": max(0, int(self._consumer_cursors.get(key, 0))),
            "owner_version": max(0, int(self._consumer_owner_versions.get(key, 0))),
            "cutover_at": str(self._consumer_cutover_at.get(key, "")),
            "cutover_event_id": max(
                0,
                int(self._consumer_cutover_event_ids.get(key, 0)),
            ),
            "has_cutover_event_id": key in self._consumer_cutover_event_ids,
        }

    async def checkpointed_enqueue_batch(
        self,
        signals: list[ProfileSignal],
        *,
        consumer: str,
        cursor: int,
        owner_version: int | None = None,
        cutover_at: str | None = None,
        cutover_event_id: int | None = None,
    ) -> IngestResult:
        """Publish buffer and consumer cursor in one locked atomic snapshot.

        External periodic ticks cannot consume these signals between enqueue
        and cursor publication because both happen while ``_ingest_lock`` is
        held. A failed state replace restores the in-memory pre-call snapshot
        and propagates; callers must not proceed to ``tick()``.
        """
        key = str(consumer).strip()
        if not key:
            raise ValueError("consumer is required")
        next_cursor = max(0, int(cursor))
        async with self._ingest_lock:
            buffer_snapshot = {
                name: LayerBuffer.from_dict(buffer.to_dict())
                for name, buffer in self._buffers.items()
            }
            cursor_snapshot = dict(self._consumer_cursors)
            version_snapshot = dict(self._consumer_owner_versions)
            cutover_snapshot = dict(self._consumer_cutover_at)
            cutover_event_id_snapshot = dict(self._consumer_cutover_event_ids)
            tombstone_snapshot = dict(self._retraction_tombstones)
            total_snapshot = self._total_ingested
            result, accepted = self._enqueue_batch_locked(signals)
            self._consumer_cursors[key] = max(
                int(self._consumer_cursors.get(key, 0)),
                next_cursor,
            )
            if owner_version is not None:
                self._consumer_owner_versions[key] = max(
                    int(self._consumer_owner_versions.get(key, 0)),
                    max(0, int(owner_version)),
                )
            if cutover_at is not None:
                self._consumer_cutover_at[key] = str(cutover_at)
            if cutover_event_id is not None:
                self._consumer_cutover_event_ids[key] = max(
                    int(self._consumer_cutover_event_ids.get(key, 0)),
                    max(0, int(cutover_event_id)),
                )
            try:
                self._save_state()
            except Exception:
                self._buffers = buffer_snapshot
                self._consumer_cursors = cursor_snapshot
                self._consumer_owner_versions = version_snapshot
                self._consumer_cutover_at = cutover_snapshot
                self._consumer_cutover_event_ids = cutover_event_id_snapshot
                self._retraction_tombstones = tombstone_snapshot
                self._total_ingested = total_snapshot
                raise
            self._observe_signals(accepted)
            return result

    async def ingest(self, signal: ProfileSignal) -> IngestResult:
        """Ingest a single signal: classify, buffer, and check thresholds."""
        return await self.ingest_batch([signal])

    async def ingest_batch(self, signals: list[ProfileSignal]) -> IngestResult:
        """Ingest multiple signals, then check all buffers for readiness.

        The whole append → consume → persist sequence holds ``_ingest_lock``:
        concurrent callers are serialized, so each drained batch runs its own
        (potentially LLM-backed) layer update without overlapping another
        batch's analysis or racing ``_save_state`` snapshots.
        """
        async with self._ingest_lock:
            result, accepted = self._enqueue_batch_locked(signals)
            # Persist the enqueue boundary before entering an LLM call. If the
            # process is interrupted while a ready layer is being analysed, a
            # reconstructed pipeline can replay the still-buffered evidence.
            self._save_state()
            self._observe_signals(accepted)
            await self._consume_ready_buffers_locked(result.layers_updated)
            self._save_state()
            return result

    def _enqueue_batch_locked(
        self, signals: list[ProfileSignal]
    ) -> tuple[IngestResult, list[ProfileSignal]]:
        """Shared locked append path for :meth:`enqueue_batch` and ingest."""
        known_ids = {
            str(item.get("id", ""))
            for buf in self._buffers.values()
            for item in buf.signals
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        accepted: list[ProfileSignal] = []
        for signal in signals:
            if signal.id in known_ids:
                continue
            known_ids.add(signal.id)
            if _is_non_profile_signal(signal):
                # Context-only collector events (hover / scroll / snapshot /
                # reshuffle / pause / seek) are consumed here: the durable row
                # exists, the consumer cursor advances past it, but no profile
                # buffer is touched and the speculators never observe it.
                continue
            accepted.append(signal)

        # Atomic retraction-discount preprocessing runs BEFORE any threshold
        # consumption (_update_layer) so a same-batch or out-of-order retraction
        # discounts the matching positive before it can be folded into a layer
        # (Phase 0 face 1/1b, invariant "atomic entry").
        self._preprocess_retractions(accepted)

        result = IngestResult()
        layers_touched: set[str] = set()

        for signal in accepted:
            for layer in signal.target_layers:
                if layer not in _BUFFERED_LAYERS:
                    continue
                buf = self._buffers.get(layer.value)
                if buf is None:
                    continue
                buf.signals.append(_serialize_signal(signal))
                threshold = self._thresholds.get(layer)
                if threshold:
                    buf.evict(threshold.max_buffer_size)
                layers_touched.add(layer.value)

            result.signals_accepted += 1
            self._total_ingested += 1

        result.layers_buffered = sorted(layers_touched)

        return result, accepted

    def _observe_signals(self, accepted: list[ProfileSignal]) -> None:
        """Publish lightweight speculation observations after durable enqueue."""
        if self._speculator or self._avoidance_speculator:
            raw_events = [
                sig.get("payload", {}) if isinstance(sig.get("payload"), dict) else {}
                for signal in accepted
                for sig in [{"payload": signal.payload}]
            ]
        else:
            raw_events = []
        if self._speculator:
            self._speculator.observe(raw_events)
        if self._avoidance_speculator:
            self._avoidance_speculator.observe(raw_events)

    async def _consume_ready_buffers_locked(
        self,
        updates: list[LayerUpdateResult],
        *,
        now: datetime | None = None,
    ) -> None:
        """Consume every ready buffer while the caller holds ``_ingest_lock``."""

        # Check thresholds and update ready layers.
        # Strong-signal types (feedback, dialogue) bypass the min_signals gate.
        ready_at = now or datetime.now()
        for layer in _BUFFERED_LAYERS:
            buf = self._buffers.get(layer.value)
            threshold = self._thresholds.get(layer)
            has_strong = buf is not None and any(
                s.get("signal_type") in _STRONG_TYPE_VALUES for s in buf.signals
            )
            if buf and threshold and self._buffer_ready(buf, threshold, ready_at, has_strong):
                update_result = await self._update_layer(layer, buf)
                if update_result:
                    updates.append(update_result)

    def _buffer_ready(
        self,
        buf: LayerBuffer,
        threshold: LayerThreshold,
        now: datetime,
        has_strong: bool,
    ) -> bool:
        """Readiness check shared by ``ingest_batch`` and ``tick``."""
        return buf.is_ready(
            threshold,
            now,
            has_strong_signal=has_strong,
            feedback_priority_threshold=self._feedback_priority_threshold,
        )

    def _preprocess_retractions(self, signals: list[ProfileSignal]) -> None:
        """Discount positives undone by a retraction, atomically at batch entry.

        Three effects (all before threshold consumption):
          1. Incoming positives whose ``(key, action)`` matches an existing
             tombstone AND whose event time precedes it are discounted (handles
             a retraction that arrived in an earlier batch — out of order).
          2. Each batch retraction discounts matching positives in this batch
             and in the existing buffers, then registers/refreshes its tombstone.
          3. Tombstones past their 24h TTL or over the 500 cap are evicted.

        Missing event times are treated conservatively (never discount) so a
        re-like after a retraction (``like → retract → like``) is preserved.
        """
        from datetime import UTC

        now = datetime.now(UTC)
        self._evict_retraction_tombstones(now)

        incoming_payloads = [sig.payload for sig in signals if isinstance(sig.payload, dict)]
        buffered_payloads = [
            payload
            for buf in self._buffers.values()
            for sig in buf.signals
            if isinstance(sig, dict) and isinstance((payload := sig.get("payload")), dict)
        ]

        # (1) Entry discount against tombstones from prior batches.
        for payload in incoming_payloads:
            self._maybe_discount_against_tombstones(payload)

        # (2) Apply this batch's retractions.
        for sig in signals:
            if not isinstance(sig.payload, dict):
                continue
            descriptor = _retraction_signal_descriptor(sig.payload)
            if descriptor is None:
                continue
            key, action, retraction_time = descriptor
            if retraction_time is None:
                # No causal reference → cannot order positives; skip (conservative).
                continue
            for payload in (*incoming_payloads, *buffered_payloads):
                positive = _positive_signal_descriptor(payload)
                if positive is None:
                    continue
                p_key, p_action, p_time = positive
                if (
                    p_key == key
                    and p_action == action
                    and p_time is not None
                    and p_time < retraction_time
                ):
                    _discount_payload(payload)
            existing = self._retraction_tombstones.get((key, action))
            if existing is None or retraction_time > existing:
                self._retraction_tombstones[(key, action)] = retraction_time

        self._evict_retraction_tombstones(now)

    def _maybe_discount_against_tombstones(self, payload: dict[str, object]) -> None:
        positive = _positive_signal_descriptor(payload)
        if positive is None:
            return
        key, action, event_time = positive
        if event_time is None:
            return
        tombstone = self._retraction_tombstones.get((key, action))
        if tombstone is not None and event_time < tombstone:
            _discount_payload(payload)

    def _evict_retraction_tombstones(self, now: datetime) -> None:
        expired = [
            marker
            for marker, retraction_time in self._retraction_tombstones.items()
            if now - retraction_time > _RETRACTION_TOMBSTONE_TTL
        ]
        for marker in expired:
            del self._retraction_tombstones[marker]
        overflow = len(self._retraction_tombstones) - _RETRACTION_TOMBSTONE_CAP
        if overflow > 0:
            oldest = sorted(self._retraction_tombstones.items(), key=lambda kv: kv[1])
            for marker, _ in oldest[:overflow]:
                del self._retraction_tombstones[marker]

    async def tick(self) -> FlushResult:
        """Consume ready buffers under the lock, then run maintenance outside it."""
        result = FlushResult()
        now = datetime.now()
        async with self._ingest_lock:
            await self._consume_ready_buffers_locked(result.layers_updated, now=now)
            # Commit a successful drain before optional maintenance. A later
            # speculator/cognition failure must never make restart replay an
            # already-applied layer update from the pre-drain snapshot.
            self._save_state()
        await self._run_tick_maintenance(result, now)
        return result

    async def tick_if_buffered(self) -> FlushResult:
        """Tick only when durable signal buffers contain recovery work.

        Event-cursor owners use this after publishing their cursor and buffer in
        one checkpoint.  The predicate is deliberately evaluated while holding
        ``_ingest_lock``: a concurrent periodic tick cannot drain the buffer
        between a separate check and this tick, causing an otherwise empty
        recovery pass to run speculator/cognition maintenance.
        """
        result = FlushResult()
        now = datetime.now()
        async with self._ingest_lock:
            if not any(buffer.signals for buffer in self._buffers.values()):
                return result
            await self._consume_ready_buffers_locked(result.layers_updated, now=now)
            # Match tick(): persist a successful drain before optional
            # maintenance so restart never replays an already-applied update.
            self._save_state()
        await self._run_tick_maintenance(result, now)
        return result

    async def _run_tick_maintenance(self, result: FlushResult, now: datetime) -> None:
        """Run non-buffer periodic maintenance outside the ingest lock."""

        # Speculator tick: expire → promote → generate.
        # Pipeline.tick runs every minute, but the speculator doesn't
        # need that cadence in steady state — once active is full and
        # nothing has changed, ticking only burns I/O and prints log
        # noise.  Only run when:
        #   (a) a layer was actually flushed in this pipeline pass — the
        #       profile materially changed, so probes might be stale
        #   (b) idle interval (30 min) has elapsed since the last tick —
        #       safety net so expire/promote still happens for users
        #       whose profile is stable but who interact with probes
        if self._speculator or self._avoidance_speculator:
            should_tick_speculator = bool(result.layers_updated) or (
                self._last_speculator_tick_at is None
                or now - self._last_speculator_tick_at >= self._speculator_idle_min_interval
            )
            if should_tick_speculator:
                if self._speculator:
                    await self._run_speculator_tick(result)
                if self._avoidance_speculator:
                    try:
                        await self._run_avoidance_speculator_tick(result)
                    except Exception:
                        logger.warning("Avoidance speculator tick failed", exc_info=True)
                self._last_speculator_tick_at = now

        # Cognition cycle: throttled awareness + insight regeneration.
        # Runs at most once per configured interval (default 12h).
        if self._cognition_cycle is not None:
            try:
                cog_result = await self._cognition_cycle.run_if_due()
                if cog_result.ran and (
                    cog_result.awareness_generated or cog_result.insight_generated
                ):
                    cog_update = LayerUpdateResult(
                        layer=OnionLayer.PORTRAIT,
                        changed=True,
                        changes=[
                            f"新增观察 {cog_result.awareness_generated} 条，"
                            f"新增洞察 {cog_result.insight_generated} 条",
                        ],
                        trigger="半日深度反思",
                        timestamp=datetime.now().isoformat(),
                    )
                    result.layers_updated.append(cog_update)
            except Exception:
                logger.exception("Cognition cycle failed during pipeline tick")

        # Profile consolidation: throttled LLM-judged dedup of like/dislike
        # topics at the 64-cap boundary (default every 12h; dirty-check and
        # no-merge memory make stable-profile ticks nearly free).
        if self._profile_consolidator is not None:
            try:
                cons_report = await self._profile_consolidator.run_if_due()
                if getattr(cons_report, "merges", None) or getattr(
                    cons_report, "rule_merges", None
                ):
                    self._record_consolidation_cognition(cons_report)
                    cons_update = LayerUpdateResult(
                        layer=OnionLayer.INTEREST,
                        changed=True,
                        changes=[
                            f"画像整理: 合并 {len(cons_report.merges)} 组同义主题、"
                            f"{len(cons_report.rule_merges)} 组同名标签",
                        ],
                        trigger="12小时画像整理",
                        timestamp=datetime.now().isoformat(),
                    )
                    result.layers_updated.append(cons_update)
            except Exception:
                logger.exception("Profile consolidation failed during pipeline tick")

    def _record_consolidation_cognition(self, report: Any) -> None:
        """Surface an applied consolidation run as a cognition update card."""
        loader = getattr(self._memory, "load_cognition_updates", None)
        saver = getattr(self._memory, "save_cognition_updates", None)
        if not callable(loader) or not callable(saver):
            return
        merges = list(getattr(report, "merges", []) or [])
        rule_count = len(getattr(report, "rule_merges", []) or [])
        like_count = sum(1 for m in merges if m.get("scope") == "likes")
        dislike_count = sum(1 for m in merges if m.get("scope") == "dislikes")
        parts: list[str] = []
        if like_count or rule_count:
            parts.append(f"兴趣合并 {like_count + rule_count} 组")
        if dislike_count:
            parts.append(f"避雷合并 {dislike_count} 组")
        if not parts:
            return
        examples = "；".join(
            f"{' / '.join(str(x) for x in m.get('members', [])[:2])} → {m.get('canonical')}"
            for m in merges[:2]
        )
        try:
            updates = loader()
            updates.insert(
                0,
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "profile_consolidation",
                    "summary": f"帮你把画像里重复的主题整理了一下：{'、'.join(parts)}",
                    "impact": "进推荐的兴趣/避雷名额不再被同义重复占用",
                    "reasoning": examples,
                    "evidence": "",
                    "context_line": "12 小时画像整理",
                    "confidence": 1.0,
                    "created_at": datetime.now().isoformat(),
                    "source": "consolidation",
                    "source_label": "画像整理",
                    "expand_hint": "summary_only",
                    "notified": False,
                },
            )
            saver(updates)
        except Exception:
            logger.debug("Failed to record consolidation cognition update", exc_info=True)

    async def flush(
        self,
        *,
        layers: frozenset[OnionLayer] | None = None,
    ) -> FlushResult:
        """Force-update specified layers regardless of thresholds."""
        result = FlushResult()
        async with self._ingest_lock:
            target_layers = layers or _BUFFERED_LAYERS
            for layer in target_layers:
                buf = self._buffers.get(layer.value)
                if buf and buf.signals:
                    update_result = await self._update_layer(layer, buf)
                    if update_result:
                        result.layers_updated.append(update_result)
            self._save_state()
        return result

    # -- Internal -------------------------------------------------------------

    async def _update_layer(
        self,
        layer: OnionLayer,
        buf: LayerBuffer,
    ) -> LayerUpdateResult | None:
        """Execute the layer-specific update and record results."""
        from openbiliclaw.soul.layer_updaters import update_layer

        signals = buf.drain()
        if not signals:
            return None

        try:
            profile = self._load_profile()
            update_result = await update_layer(
                layer=layer,
                signals=signals,
                profile=profile,
                memory=self._memory,
                preference_analyzer=self._preference_analyzer,
                profile_builder=self._profile_builder,
                embedding_service=self._embedding_service,
                llm_service=getattr(self._preference_analyzer, "registry", None),
                posture_gate=self._posture_gate,
                feedback_hooks=self._feedback_hooks,
            )
        except Exception:
            logger.exception("Failed to update layer %s", layer.value)
            # Put signals back so they're not lost
            buf.signals = signals + buf.signals
            return None

        buf.last_updated_at = datetime.now().isoformat()
        buf.update_count += 1

        if update_result.changed:
            self._save_profile(profile)
            self._record_changelog(update_result)

            # Trigger portrait regeneration if deep layers changed
            if layer in _PORTRAIT_TRIGGER_LAYERS:
                await self._regenerate_portrait(profile)

        # Feedback-batch post-write privileges. Deliberately AFTER
        # ``_save_profile``: a gated soul rebuild rewrites the whole soul layer
        # and must not be overwritten by this layer's own profile snapshot.
        # Runs whether or not the layer reported ``changed`` — the old batch
        # gated the rebuild on ``_preference_changed_significantly``, not on the
        # rendered change list. Best-effort: never breaks the layer update.
        if update_result.feedback_context and self._feedback_hooks is not None:
            try:
                await self._feedback_hooks.after_update(**update_result.feedback_context)
            except Exception:
                logger.exception("Feedback-batch post-update hook failed")

        return update_result

    def _load_profile(self) -> Any:
        """Load current OnionProfile from soul layer."""
        from openbiliclaw.soul.profile import OnionProfile

        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            return OnionProfile()
        return OnionProfile.from_dict(soul_data)

    def _save_profile(self, profile: Any) -> None:
        """Persist profile to soul layer and sync files."""
        soul_layer = self._memory.get_layer("soul")
        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()
        self._memory.sync_profile_files(profile)

    async def _regenerate_portrait(self, profile: Any) -> None:
        """Regenerate personality_portrait after Core/Values change."""
        from openbiliclaw.soul.layer_updaters import regenerate_portrait

        try:
            new_portrait = await regenerate_portrait(
                profile=profile,
                profile_builder=self._profile_builder,
                memory=self._memory,
            )
            if new_portrait:
                profile.personality_portrait = new_portrait
                self._save_profile(profile)
        except Exception:
            logger.exception("Failed to regenerate portrait")

    def _record_changelog(self, result: LayerUpdateResult) -> None:
        """Write a changelog entry for a layer update."""
        from openbiliclaw.soul.profile_renderer import render_changelog_entry

        entry = render_changelog_entry(
            timestamp=result.timestamp or datetime.now().isoformat(),
            layer=result.layer.value,
            changes=result.changes,
            trigger=result.trigger,
            evidence=result.evidence,
        )
        self._memory.append_changelog(entry)

    async def _run_speculator_tick(self, result: FlushResult) -> None:
        """Run speculator lifecycle: expire, promote, generate."""
        from openbiliclaw.soul.interest_writeback import merge_confirmed_interest

        profile = self._load_profile()
        load_runtime_state = getattr(self._memory, "load_discovery_runtime_state", None)

        def _load_feedback_history() -> object:
            if not callable(load_runtime_state):
                return []
            try:
                runtime_state = load_runtime_state()
                if isinstance(runtime_state, dict):
                    return runtime_state.get("probe_feedback_history", [])
            except Exception:
                logger.debug("Failed to load probe feedback history", exc_info=True)
            return []

        feedback_history = _load_feedback_history()
        tick = self._speculator.tick  # type: ignore[union-attr]
        try:
            tick_result = await tick(
                profile,
                feedback_history=feedback_history,
                feedback_history_loader=_load_feedback_history,
            )
        except TypeError:
            try:
                tick_result = await tick(profile, feedback_history=feedback_history)
            except TypeError:
                tick_result = await tick(profile)

        # Speculation stalemate → confusion (Phase 2): a partially-confirmed
        # expiry is an unresolved "看不懂", not a clean rejection. Best-effort —
        # never breaks the speculator tick.
        stalemate = getattr(tick_result, "stalemate", None)
        if stalemate:
            try:
                from openbiliclaw.soul.confusion import ConfusionManager
                from openbiliclaw.soul.ledger import ProfileLedger

                database = getattr(self._memory, "_database", None)
                manager = ConfusionManager(database, ledger=ProfileLedger(database))
                for spec in stalemate:
                    manager.create_from_speculation_stalemate(
                        domain=str(getattr(spec, "domain", "")),
                        confirmation_count=int(getattr(spec, "confirmation_count", 0)),
                        confirmation_threshold=int(getattr(spec, "confirmation_threshold", 3)),
                    )
            except Exception:
                logger.debug("Failed to raise stalemate confusions", exc_info=True)

        # Promote confirmed speculations into the interest layer
        if tick_result.promoted:
            for spec in tick_result.promoted:
                specifics = [
                    str(getattr(specific, "name", "")).strip()
                    for specific in getattr(spec, "specifics", [])
                    if str(getattr(specific, "name", "")).strip()
                ]
                source = str(getattr(spec, "confirmation_source", "") or "speculated")
                merge_confirmed_interest(
                    profile,
                    domain=str(getattr(spec, "domain", "")),
                    specifics=specifics,
                    source=source,
                    first_seen=str(getattr(spec, "created_at", "")),
                    last_seen=str(getattr(spec, "confirmed_at", "")) or datetime.now().isoformat(),
                )

            self._save_profile(profile)
            changes = [f"猜测兴趣转正: {s.domain}" for s in tick_result.promoted]
            update_result = LayerUpdateResult(
                layer=OnionLayer.INTEREST,
                changed=True,
                changes=changes,
                signals_consumed=0,
                trigger="猜测兴趣确认",
                evidence=", ".join(
                    f"{s.domain}({s.confirmation_count}次确认)" for s in tick_result.promoted
                ),
                timestamp=datetime.now().isoformat(),
            )
            result.layers_updated.append(update_result)
            self._record_changelog(update_result)

    async def _run_avoidance_speculator_tick(self, result: FlushResult) -> None:
        """Run avoidance speculator lifecycle and write confirmed topics."""
        profile = self._load_profile()
        load_runtime_state = getattr(self._memory, "load_discovery_runtime_state", None)

        def _load_feedback_history() -> object:
            if not callable(load_runtime_state):
                return []
            try:
                runtime_state = load_runtime_state()
                if isinstance(runtime_state, dict):
                    return runtime_state.get("avoidance_probe_feedback_history", [])
            except Exception:
                logger.debug("Failed to load avoidance probe feedback history", exc_info=True)
            return []

        feedback_history = _load_feedback_history()
        tick = self._avoidance_speculator.tick  # type: ignore[union-attr]
        try:
            tick_result = await tick(
                profile,
                feedback_history=feedback_history,
                feedback_history_loader=_load_feedback_history,
            )
        except TypeError:
            try:
                tick_result = await tick(profile, feedback_history=feedback_history)
            except TypeError:
                tick_result = await tick(profile)

        if not tick_result.promoted:
            return

        topics: list[str] = []
        for avoidance in tick_result.promoted:
            topics.extend(topics_for_confirmed_avoidance(avoidance))
        if not topics:
            return

        changes = await apply_new_dislikes(
            memory=self._memory,
            database=getattr(self._memory, "_database", None),
            embedding_service=self._embedding_service,
            llm_service=getattr(self._preference_analyzer, "registry", None),
            topics=topics,
        )
        if not changes:
            return

        update_result = LayerUpdateResult(
            layer=OnionLayer.INTEREST,
            changed=True,
            changes=changes,
            signals_consumed=0,
            trigger="避雷方向确认",
            evidence=", ".join(
                f"{item.domain}({item.confirmation_count}次确认)" for item in tick_result.promoted
            ),
            timestamp=datetime.now().isoformat(),
        )
        result.layers_updated.append(update_result)
        self._record_changelog(update_result)

    def _save_state(self) -> None:
        """Persist buffer state to disk."""
        data_dir = getattr(self._memory, "_data_dir", None)
        if data_dir:
            save_pipeline_state(
                data_dir,
                self._buffers,
                self._total_ingested,
                consumer_cursors=self._consumer_cursors,
                consumer_owner_versions=self._consumer_owner_versions,
                consumer_cutover_at=self._consumer_cutover_at,
                consumer_cutover_event_ids=self._consumer_cutover_event_ids,
            )
