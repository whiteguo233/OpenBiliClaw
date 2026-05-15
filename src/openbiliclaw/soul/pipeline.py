"""Profile Update Pipeline — single entry point for all profile-affecting signals.

All behavioral events, feedback, dialogue insights, and account sync data
flow through `ProfileUpdatePipeline.ingest()`. The pipeline classifies each
signal by target onion layer, buffers it, and triggers per-layer updates
when thresholds are met.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
    from openbiliclaw.soul.profile_builder import ProfileBuilder
    from openbiliclaw.soul.speculator import InterestSpeculator

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
    ) -> bool:
        """Check if this buffer has enough signals and enough time has passed.

        If *has_strong_signal* is True the min_signals gate is reduced to 1,
        so feedback and dialogue signals update the profile immediately.
        """
        effective_min = 1 if has_strong_signal else threshold.min_signals
        if len(self.signals) < effective_min:
            return False
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
    SignalType.FEEDBACK: frozenset(
        {
            OnionLayer.INTEREST,
            OnionLayer.SURFACE,
            OnionLayer.VALUES,
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

# Dialogue insight kind → target layers
_DIALOGUE_INSIGHT_KIND_MAP: dict[str, frozenset[OnionLayer]] = {
    "interest": frozenset({OnionLayer.INTEREST}),
    "dislike": frozenset({OnionLayer.INTEREST}),
    "value": frozenset({OnionLayer.VALUES}),
    "goal": frozenset({OnionLayer.ROLE}),
    "state": frozenset({OnionLayer.CORE}),
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

# Layers that trigger portrait regeneration when changed
_PORTRAIT_TRIGGER_LAYERS = frozenset({OnionLayer.CORE, OnionLayer.VALUES})

# Layers that participate in buffering (PORTRAIT is conditional, not buffered)
_BUFFERED_LAYERS = frozenset(
    {
        OnionLayer.SURFACE,
        OnionLayer.INTEREST,
        OnionLayer.ROLE,
        OnionLayer.VALUES,
        OnionLayer.CORE,
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


# ---------------------------------------------------------------------------
# Signal factory helpers
# ---------------------------------------------------------------------------


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
    result: list[ProfileSignal] = []
    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "")
        if event_type in _ENGAGEMENT_TYPES:
            sig_type = SignalType.ENGAGEMENT_EVENT
        else:
            sig_type = SignalType.BEHAVIOR_EVENT
        result.append(_make_signal(sig_type, "events", dict(event)))
    return result


def signal_from_feedback(
    feedback_type: str,
    title: str,
    note: str = "",
) -> ProfileSignal:
    """Convert a recommendation feedback action into a ProfileSignal."""
    return _make_signal(
        SignalType.FEEDBACK,
        "feedback",
        {"feedback_type": feedback_type, "title": title, "note": note},
    )


def signals_from_dialogue(
    candidates: list[dict[str, object]],
) -> list[ProfileSignal]:
    """Convert dialogue-derived insight candidates into ProfileSignals.

    Only candidates that have reached the readiness threshold
    (confidence >= 0.8, occurrences >= 2) should be passed here.
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
    return _make_signal(SignalType.RECOMMENDATION_CLICK, "recommendation", payload)


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


def load_pipeline_state(data_dir: Path) -> dict[str, LayerBuffer]:
    """Load pipeline buffer state from disk."""
    state_path = data_dir / "memory" / "pipeline_state.json"
    buffers: dict[str, LayerBuffer] = {}
    for layer in _BUFFERED_LAYERS:
        buffers[layer.value] = LayerBuffer(layer=layer)

    if not state_path.exists():
        return buffers

    try:
        with open(state_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return buffers

    raw_buffers = data.get("buffers")
    if not isinstance(raw_buffers, dict):
        return buffers

    for key, raw_buf in raw_buffers.items():
        if isinstance(raw_buf, dict) and key in buffers:
            buffers[key] = LayerBuffer.from_dict(raw_buf)

    return buffers


def save_pipeline_state(
    data_dir: Path,
    buffers: dict[str, LayerBuffer],
    total_ingested: int = 0,
) -> None:
    """Persist pipeline buffer state to disk."""
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    state_path = memory_dir / "pipeline_state.json"

    payload = {
        "version": 1,
        "buffers": {key: buf.to_dict() for key, buf in buffers.items()},
        "last_saved_at": datetime.now().isoformat(),
        "total_signals_ingested": total_ingested,
    }
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# ProfileUpdatePipeline
# ---------------------------------------------------------------------------


class ProfileUpdatePipeline:
    """Consolidates all profile update signals into a single entry point.

    Usage:
        pipeline = ProfileUpdatePipeline(memory=..., preference_analyzer=..., ...)
        await pipeline.ingest(signal)       # Buffer a signal
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
        embedding_service: Any | None = None,
        cognition_cycle: Any | None = None,
    ) -> None:
        self._memory = memory
        self._preference_analyzer = preference_analyzer
        self._profile_builder = profile_builder
        self._thresholds = thresholds or dict(DEFAULT_THRESHOLDS)
        self._speculator = speculator
        self._embedding_service = embedding_service
        self._cognition_cycle = cognition_cycle
        data_dir = getattr(memory, "_data_dir", None)
        self._buffers = (
            load_pipeline_state(data_dir)
            if data_dir
            else {layer.value: LayerBuffer(layer=layer) for layer in _BUFFERED_LAYERS}
        )
        self._total_ingested = 0
        # Track when we last ran the speculator tick so we can throttle
        # idle ticks while still letting layer-updates trigger fresh
        # speculator passes.  See `tick()` body for usage.
        self._last_speculator_tick_at: datetime | None = None
        # Minimum interval between speculator ticks when no layer was
        # updated.  Pipeline.tick itself runs every minute, but the
        # speculator only needs periodic expire/promote checks; idle
        # cadence at 30 minutes is plenty.
        self._speculator_idle_min_interval = timedelta(minutes=30)

    def set_embedding_service(self, embedding_service: Any) -> None:
        """Attach or replace the embedding service for semantic operations."""
        self._embedding_service = embedding_service

    def set_cognition_cycle(self, cognition_cycle: Any) -> None:
        """Attach or replace the cognition cycle runner."""
        self._cognition_cycle = cognition_cycle

    # -- Public API -----------------------------------------------------------

    async def ingest(self, signal: ProfileSignal) -> IngestResult:
        """Ingest a single signal: classify, buffer, and check thresholds."""
        return await self.ingest_batch([signal])

    async def ingest_batch(self, signals: list[ProfileSignal]) -> IngestResult:
        """Ingest multiple signals, then check all buffers for readiness."""
        result = IngestResult()
        layers_touched: set[str] = set()

        for signal in signals:
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

        # Speculator observation (lightweight keyword matching)
        if self._speculator:
            raw_events = [
                sig.get("payload", {}) if isinstance(sig.get("payload"), dict) else {}
                for signal in signals
                for sig in [{"payload": signal.payload}]
            ]
            self._speculator.observe(raw_events)

        # Check thresholds and update ready layers.
        # Strong-signal types (feedback, dialogue) bypass the min_signals gate.
        now = datetime.now()
        for layer in _BUFFERED_LAYERS:
            buf = self._buffers.get(layer.value)
            threshold = self._thresholds.get(layer)
            has_strong = buf is not None and any(
                s.get("signal_type") in _STRONG_TYPE_VALUES for s in buf.signals
            )
            if buf and threshold and buf.is_ready(threshold, now, has_strong_signal=has_strong):
                update_result = await self._update_layer(layer, buf)
                if update_result:
                    result.layers_updated.append(update_result)

        self._save_state()
        return result

    async def tick(self) -> FlushResult:
        """Periodic check: update any layers whose buffers are ready."""
        result = FlushResult()
        now = datetime.now()
        for layer in _BUFFERED_LAYERS:
            buf = self._buffers.get(layer.value)
            threshold = self._thresholds.get(layer)
            has_strong = buf is not None and any(
                s.get("signal_type") in _STRONG_TYPE_VALUES for s in buf.signals
            )
            if buf and threshold and buf.is_ready(threshold, now, has_strong_signal=has_strong):
                update_result = await self._update_layer(layer, buf)
                if update_result:
                    result.layers_updated.append(update_result)

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
        if self._speculator:
            should_tick_speculator = bool(result.layers_updated) or (
                self._last_speculator_tick_at is None
                or now - self._last_speculator_tick_at >= self._speculator_idle_min_interval
            )
            if should_tick_speculator:
                await self._run_speculator_tick(result)
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

        self._save_state()
        return result

    async def flush(
        self,
        *,
        layers: frozenset[OnionLayer] | None = None,
    ) -> FlushResult:
        """Force-update specified layers regardless of thresholds."""
        result = FlushResult()
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
        from openbiliclaw.soul.profile import InterestDomain

        profile = self._load_profile()
        feedback_history: object = []
        load_runtime_state = getattr(self._memory, "load_discovery_runtime_state", None)
        if callable(load_runtime_state):
            try:
                runtime_state = load_runtime_state()
                if isinstance(runtime_state, dict):
                    feedback_history = runtime_state.get("probe_feedback_history", [])
            except Exception:
                logger.debug("Failed to load probe feedback history", exc_info=True)
        tick = self._speculator.tick  # type: ignore[union-attr]
        try:
            tick_result = await tick(profile, feedback_history=feedback_history)
        except TypeError:
            tick_result = await tick(profile)

        # Promote confirmed speculations into the interest layer
        if tick_result.promoted:
            for spec in tick_result.promoted:
                profile.interest.likes.append(
                    InterestDomain(
                        domain=spec.domain,
                        weight=0.3,
                        source="speculated",
                        first_seen=spec.created_at,
                        last_seen=datetime.now().isoformat(),
                    )
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

    def _save_state(self) -> None:
        """Persist buffer state to disk."""
        data_dir = getattr(self._memory, "_data_dir", None)
        if data_dir:
            save_pipeline_state(data_dir, self._buffers, self._total_ingested)
