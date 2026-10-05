"""User Soul Engine — the heart of OpenBiliClaw.

Transforms raw behavioral data into deep, layered understanding of a person.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from openbiliclaw.llm.service import ModuleOverride, SupportsComplete
    from openbiliclaw.memory.manager import MemoryManager

from openbiliclaw.llm.service import LLMService

from .avoidance_speculator import AvoidanceSpeculator
from .awareness_analyzer import AwarenessAnalyzer
from .cognition_cycle import (
    _AWARENESS_EVENT_BATCH_SIZE as _DEFAULT_AWARENESS_EVENT_BATCH_SIZE,
)
from .cognition_cycle import (
    _COGNITION_MAX_TOKENS as _DEFAULT_COGNITION_MAX_TOKENS,
)
from .cognition_cycle import (
    _INSIGHT_NOTE_BATCH_SIZE as _DEFAULT_INSIGHT_NOTE_BATCH_SIZE,
)
from .cognition_cycle import (
    DEFAULT_MIN_INTERVAL_SECONDS as _DEFAULT_COG_INTERVAL,
)
from .cognition_cycle import (
    CognitionCycle,
)
from .confusion import ConfusionManager, apply_confusion_freeze
from .consolidator import ProfileConsolidator
from .dialogue_anchor import ENTRY_CONFUSION_PROMPT, DialogueAnchor, DialogueAnchorManager
from .dialogue_insight_analyzer import (
    DialogueInsightAnalysisError,
    DialogueInsightAnalyzer,
)
from .dialogue_learn_queue import (
    ANCHOR_NOT_APPLICABLE,
    AnchorAbsent,
    AnchorAdmissionSnapshot,
    AnchorFailed,
    AnchorMutationTerminal,
    AnchorNotApplicable,
    AnchorPersisted,
    AnchorReserved,
    DialogueDispatchResult,
    DialogueJob,
    DialogueJobKind,
    DialogueJobResult,
    DialogueSettlementQueue,
)
from .event_prompt_views import normalize_cognition_input_view
from .identity import build_hash8_map
from .insight_analyzer import InsightAnalyzer
from .ledger import ProfileLedger
from .overrides import ProfileOverrides, apply_edit, apply_overrides
from .pipeline import (
    FeedbackConsumerHooks,
    OnionLayer,
    ProfileSignal,
    ProfileUpdatePipeline,
    SignalType,
    is_content_feedback_event,
    migrate_pipeline_deep_buffers,
    signal_from_event,
    signal_from_feedback,
    signal_from_recommendation_click,
)
from .posture_gate import ACCEPT, GateDecision, PostureGate
from .preference_analyzer import (
    DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
    INIT_COGNITION_CONTEXT_KEY,
    PreferenceAnalyzer,
)
from .profile import (
    AwarenessNote,
    InsightHypothesis,
    OnionProfile,
    awareness_note_from_dict,
    awareness_note_to_dict,
    insight_hypothesis_from_dict,
    insight_hypothesis_to_dict,
)
from .profile_builder import ProfileBuilder
from .speculator import InterestSpeculator

logger = logging.getLogger(__name__)

# Dialogue candidate kinds that write DEEP profile layers and therefore pass the
# posture gate (access point ①). interest/dislike take the fast line unchanged.
_DEEP_CANDIDATE_KINDS = frozenset({"goal", "value", "state"})

# Soul-rebuild triggers (access point ③, generalized — spec r3/F4). Each drives
# a full gated rebuild but carries a distinct ledger write point so the audit
# trail distinguishes what caused it: dialogue learning, a feedback batch with a
# significant preference shift (P2 — previously ungated), or a batch of
# newly-confirmed hypotheses (the pending-rebuild state machine).
_REBUILD_TRIGGER_DIALOGUE = "dialogue"
_REBUILD_TRIGGER_FEEDBACK_BATCH = "feedback_batch"
_REBUILD_TRIGGER_CONFIRMED_HYPOTHESES = "confirmed_hypotheses"
_REBUILD_WRITE_POINT: dict[str, str] = {
    _REBUILD_TRIGGER_DIALOGUE: "dialogue_soul_rebuild",
    _REBUILD_TRIGGER_FEEDBACK_BATCH: "feedback_soul_rebuild",
    _REBUILD_TRIGGER_CONFIRMED_HYPOTHESES: "hypotheses_soul_rebuild",
}

# A hypothesis only shapes a soul rebuild once it is validated AND confident
# (spec invariant 3 / r3/F1). Rejected or unvalidated hypotheses are invisible
# to every rebuild, so a reject's next rebuild squeezes an old conclusion out.
_REBUILD_MIN_CONFIDENCE = 0.75
# Autonomous deep influence (2026-07-27, user decision): a hypothesis the user
# never ruled on may shape a gated soul rebuild WITHOUT explicit confirmation,
# once behaviour has corroborated it long enough. The bar is deliberately
# higher than the user-confirmed path on every axis:
# - confidence >= 0.8 (vs 0.75): on real cognition output fresh hypotheses land
#   at 0.5-0.75; only repeated cross-cycle corroboration pushes one to 0.8+.
# - age >= 7 days: one enthusiastic afternoon must not rewrite the deep layer;
#   seven days spans multiple cognition cycles and usage moods.
# - evidence >= 3: a hypothesis resting on a single observation stays a guess.
# A user rejection (user_verdict == "rejected") blocks autonomy permanently,
# and every autonomous rebuild still passes the posture gate. Recalibrate the
# confidence bar after any provider/model swap (pitfall rule 3).
_AUTO_VALIDATE_MIN_CONFIDENCE = 0.8
_AUTO_VALIDATE_MIN_AGE_DAYS = 7
_AUTO_VALIDATE_MIN_EVIDENCE = 3
# Fast tier (user decision 2026-07-27, "置信度非常高的话可以直接更新"): near-
# certainty waives the tenure bar, nothing else. On real cognition output a
# fresh hypothesis lands at 0.5-0.75 and 0.8+ already takes repeated cross-
# cycle corroboration, so 0.95 is only reachable when the model is essentially
# certain across many corroborating merges. The evidence floor, the untouched-
# by-the-user requirement, the posture gate and the rejection veto all still
# apply — speed is the only thing this tier buys.
_AUTO_VALIDATE_FAST_MIN_CONFIDENCE = 0.95
_AUTO_HYPOTHESIS_REF_PREFIX = "auto_hypothesis:"
# How many of the events init just recorded may be cited by a persisted draft.
# Attribution here is per-round (the model was never asked which note came from
# which event), so the notes are flagged approximate; the cap only keeps the
# citation list from growing with the whole history.
_INIT_DRAFT_EVIDENCE_CAP = 300


def _init_draft_evidence(value: object) -> list[str]:
    """Normalise a draft's evidence list, dropping blanks and non-strings."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _clamp_init_draft_confidence(value: object) -> float:
    """Coerce a draft confidence into [0, 1], defaulting to 0.5."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, round(number, 4)))


# Debounce between a confirm/reject migration and the gated rebuild it schedules
# (spec r3/F3). 6h sits between conversational cadence and the 12h cognition
# loop — first-round calibration, re-tune after the first production month
# (pitfall #3).
_DEEP_REBUILD_DEBOUNCE_HOURS = 6
# Bounded retry for a pending rebuild that keeps hitting a transient LLM/parse
# error (is_error path). After this many failures the marker is cleared with a
# WARNING so a persistently broken provider can't wedge the pending state.
_REBUILD_MAX_RETRIES = 2
# Cap on the confirmed-hypothesis list carried in the gate snapshot context.
_REBUILD_CONTEXT_HYPOTHESIS_CAP = 12

# First-round calibration (2026-07-22): 0.5 rejects clear paraphrases without
# suppressing a side topic that merely shares one generic word. Recalibrate
# after the first production month or any tokenizer/model swap.
#
# Known bound (measured 2026-07-26): this is a *lexical* defence. It reliably
# catches restatements that reuse wording ("用户喜欢深度技术内容" vs
# "喜欢深度技术内容" scores 0.78) and reliably misses semantic duplicates that
# reword ("用户喜欢深度技术内容" vs "用户偏好硬核原理讲解" scores 0.06). A
# bge-m3 cosine pass was calibrated as a second layer and rejected: over five
# duplicate and five side-topic pairs the classes were 0.599–0.796 and
# 0.446–0.569, i.e. a 0.03-wide separation band. No threshold in that band is
# trustworthy, and a false positive (silently discarding a genuine side remark
# the user just made) costs more than the false negative it would prevent (one
# redundant candidate, which downstream consolidation already merges). If this
# is revisited, calibrate on real dialogue transcripts rather than synthetic
# pairs, and keep the asymmetry in mind: prefer under- to over-filtering.
_ANCHOR_CANDIDATE_JACCARD_THRESHOLD = 0.5
# Product calibration (2026-07-22): one clarification is enough to distinguish
# hesitation from avoidance without turning the dialogue into an interrogation.
_ANCHOR_AMBIGUOUS_FOLLOW_UP_LIMIT = 1
_CHINESE_STOPWORDS = frozenset("的了是在我有")
_ENGLISH_STOPWORDS = frozenset(
    {"a", "the", "is", "am", "are", "of", "to", "and", "i", "have", "in"}
)
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ENGLISH_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ANCHOR_RELATIONS_BY_KIND = {
    "hypothesis": frozenset({"support", "contradict", "revise", "ambiguous", "unrelated"}),
    "confusion": frozenset({"answer", "ambiguous", "unrelated"}),
}
_CONFUSION_SETTLEMENT_RESOLUTIONS = frozenset({"real_interest", "proxy_behavior", "dismissed"})


def _dialogue_anchor_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFC", value).lower()
    tokens = {
        token for token in _ENGLISH_TOKEN_RE.findall(normalized) if token not in _ENGLISH_STOPWORDS
    }
    for run in _CJK_RUN_RE.findall(normalized):
        filtered = "".join(character for character in run if character not in _CHINESE_STOPWORDS)
        tokens.update(filtered[index : index + 2] for index in range(max(0, len(filtered) - 1)))
    return tokens


def _dialogue_anchor_jaccard(left: str, right: str) -> float:
    left_tokens = _dialogue_anchor_tokens(left)
    right_tokens = _dialogue_anchor_tokens(right)
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _memory_database(memory: Any) -> Any | None:
    """Resolve the SQLite database handle a memory manager owns (may be None)."""
    return getattr(memory, "_database", None)


def _as_dict_list(raw_value: object) -> list[dict[str, object]]:
    if not isinstance(raw_value, list):
        return []
    return [item for item in raw_value if isinstance(item, dict)]


SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
    "manual": "手动编辑",
}

# Human-readable labels for manual-edit cognition summaries, keyed by the
# editable onion field path / interest polarity.
_MANUAL_EDIT_LABELS = {
    "personality_portrait": "人格画像",
    "core.core_traits": "核心特质",
    "core.deep_needs": "深层需求",
    "values_layer.values": "价值观",
    "values_layer.motivational_drivers": "内在驱动",
    "surface.cognitive_style": "认知风格",
    "interest.favorite_up_users": "常看 UP 主",
    "role.life_stage": "人生阶段",
    "role.current_phase": "当前阶段",
    "likes": "喜欢",
    "dislikes": "不喜欢",
    "surface.exploration_openness": "探索开放度",
    "surface.style.quality_sensitivity": "画质敏感度",
    "surface.style.humor_preference": "幽默偏好",
    "surface.style.depth_preference": "深度偏好",
}

_FEEDBACK_ANALYSIS_METADATA_KEYS = frozenset(
    {
        "recommendation_id",
        "bvid",
        "aid",
        "content_id",
        "content_url",
        "source_platform",
        "feedback_type",
        "feedback_note",
        "reaction",
        "up_name",
        "author",
        "topic_label",
        "watch_seconds",
        "video_duration_seconds",
        "signal_strength",
    }
)

_PROFILE_EVENT_CONSUMER = "profile_events"
_CONTENT_FEEDBACK_CONSUMER = "content_feedback"
_PROFILE_EVENT_BATCH_LIMIT = 200


class SoulProfileNotInitializedError(Exception):
    """Raised when the soul layer has not been initialized yet."""


class SoulEngine:
    """Engine for building and maintaining deep user understanding.

    The Soul Engine orchestrates the transformation of raw behavioral data
    through the five-layer memory architecture:
      Event → Preference → Awareness → Insight → Soul

    It is responsible for:
    1. Analyzing new behavioral events
    2. Updating preference patterns
    3. Writing daily awareness notes
    4. Generating insight hypotheses
    5. Maintaining the soul-level personality portrait
    """

    def __init__(
        self,
        llm: SupportsComplete,
        memory: MemoryManager,
        *,
        embedding_service: Any | None = None,
        cognition_cycle_interval_seconds: int | None = None,
        awareness_event_batch_size: int = _DEFAULT_AWARENESS_EVENT_BATCH_SIZE,
        insight_note_batch_size: int = _DEFAULT_INSIGHT_NOTE_BATCH_SIZE,
        cognition_max_tokens: int = _DEFAULT_COGNITION_MAX_TOKENS,
        usage_recorder: Any | None = None,
        satisfaction_filter_enabled: bool = True,
        preference_prompt_view: str = "legacy",
        awareness_prompt_view: str = "compact-v1",
        insight_prompt_view: str = "legacy",
        module_overrides: Mapping[str, ModuleOverride] | None = None,
        llm_concurrency: int = 3,
        llm_concurrency_gate: Any | None = None,
        speculation_interval_minutes: int = 10,
        speculation_ttl_days: int = 3,
        speculation_cooldown_days: int = 7,
        speculation_confirmation_threshold: int = 3,
        speculation_max_active: int = 5,
        speculation_max_primary_interests: int = 15,
        speculation_max_secondary_interests: int = 60,
        avoidance_speculation_interval_minutes: int = 10,
        avoidance_speculation_ttl_days: int = 3,
        avoidance_speculation_cooldown_days: int = 7,
        avoidance_speculation_confirmation_threshold: int = 3,
        avoidance_speculation_max_active: int = 5,
        speculator_idle_interval_minutes: int = 30,
        profile_consolidation_enabled: bool = True,
        profile_consolidation_interval_hours: int = 12,
        profile_consolidation_like_target_upper: int = 512,
        profile_consolidation_like_target_soft: int = 450,
        profile_consolidation_archive_enabled: bool = True,
        feedback_batch_threshold: int = 3,
        unified_interest_line: bool = False,
        posture_gate_mode: str = "shadow",
        posture_gate_force_enforce: bool = False,
        reply_style: str = "",
        dialogue_tone_prompt: str = "",
        database: Any | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._satisfaction_filter_enabled = satisfaction_filter_enabled
        # Free-text reply-style instruction (issue #255), forwarded into the
        # dialogue service and the profile builder.
        self._reply_style = " ".join(str(reply_style or "").split())
        # Free-text full replacement for the dialogue prompt's tone block.
        # Strip only the outer whitespace — interior newlines are meaningful.
        self._dialogue_tone_prompt = str(dialogue_tone_prompt or "").strip()
        self._preference_prompt_view = normalize_cognition_input_view(preference_prompt_view)
        self._awareness_prompt_view = normalize_cognition_input_view(awareness_prompt_view)
        self._insight_prompt_view = normalize_cognition_input_view(insight_prompt_view)
        self._feedback_batch_threshold = max(1, feedback_batch_threshold)
        # Unified interest line kill switch (spec 2026-07-27). False (Wave A
        # default) keeps feedback on the legacy batch only — turning both on
        # would let the same feedback be counted twice.
        self._unified_interest_line = bool(unified_interest_line)
        # Monotonic count of gated soul rebuilds driven by a pipeline feedback
        # batch. The shim snapshots it around a tick so its ``profile_rebuilt``
        # return key keeps the legacy meaning without threading a result object
        # back out through the pipeline's best-effort hook boundary.
        self._pipeline_feedback_rebuilds = 0
        self._feedback_batch_lock = asyncio.Lock()
        self._profile_event_lock = asyncio.Lock()
        # Pending confirmed-hypotheses rebuild state machine (spec r3/F3). The
        # lock guards read-modify-write of the persisted marker; ``_rebuild_running``
        # prevents overlapping builds while the lock is released for the long
        # LLM build (compare-and-swap on ``set_at`` reconciles a concurrent
        # re-mark). Restart recovery is automatic: the marker persists to disk
        # and ``_rebuild_running`` resets to False on construction.
        self._rebuild_pending_lock = asyncio.Lock()
        self._rebuild_running = False
        self._module_overrides = dict(module_overrides or {})
        self._llm_concurrency = llm_concurrency
        self._llm_concurrency_gate = llm_concurrency_gate
        # Pass usage_recorder through so internal LLM calls
        # (preference / awareness / insight / profile_builder / speculator
        # / dialogue_insight) appear in the cost ledger with their caller
        # tags. Without this, the entire ``soul.*`` namespace was
        # invisible in `openbiliclaw cost --by caller` and bypassed the
        # empty-content guard in LLMService — speculator failures showed
        # up as silent "0 new generations" instead of explicit WARNs.
        self._llm_service: LLMService = LLMService(
            registry=llm,
            memory=memory,
            usage_recorder=usage_recorder,
            module_overrides=self._module_overrides,
            concurrency=llm_concurrency,
            concurrency_gate=llm_concurrency_gate,
            reply_style=self._reply_style,
            dialogue_tone_prompt=self._dialogue_tone_prompt,
        )
        self._awareness_analyzer = AwarenessAnalyzer(
            self._llm_service,
            plain_prompt_view="legacy",
            confusions_prompt_view=self._awareness_prompt_view,
        )
        self._dialogue_insight_analyzer = DialogueInsightAnalyzer(self._llm_service)
        self._insight_analyzer = InsightAnalyzer(
            self._llm_service,
            cognition_prompt_view=self._insight_prompt_view,
        )
        self._preference_analyzer = PreferenceAnalyzer(
            self._llm_service,
            satisfaction_filter_enabled=satisfaction_filter_enabled,
            embedding_service=embedding_service,
            cognition_prompt_view=self._preference_prompt_view,
        )
        self._profile_builder = ProfileBuilder(self._llm_service, reply_style=self._reply_style)
        data_dir = getattr(memory, "_data_dir", None)
        self._speculator = InterestSpeculator(
            llm_service=self._llm_service,
            data_dir=data_dir,
            generation_interval_minutes=speculation_interval_minutes,
            default_ttl_days=speculation_ttl_days,
            cooldown_days=speculation_cooldown_days,
            confirmation_threshold=speculation_confirmation_threshold,
            max_active=speculation_max_active,
            max_primary_interests=speculation_max_primary_interests,
            max_secondary_interests=speculation_max_secondary_interests,
        )
        self._avoidance_speculator = AvoidanceSpeculator(
            llm_service=self._llm_service,
            data_dir=data_dir,
            generation_interval_minutes=avoidance_speculation_interval_minutes,
            default_ttl_days=avoidance_speculation_ttl_days,
            cooldown_days=avoidance_speculation_cooldown_days,
            confirmation_threshold=avoidance_speculation_confirmation_threshold,
            max_active=avoidance_speculation_max_active,
        )
        self._embedding_service = embedding_service
        self._cognition_cycle = CognitionCycle(
            memory=memory,
            awareness_analyzer=self._awareness_analyzer,
            insight_analyzer=self._insight_analyzer,
            min_interval_seconds=(
                cognition_cycle_interval_seconds
                if cognition_cycle_interval_seconds is not None
                else _DEFAULT_COG_INTERVAL
            ),
            awareness_event_batch_size=awareness_event_batch_size,
            insight_note_batch_size=insight_note_batch_size,
            cognition_max_tokens=cognition_max_tokens,
            # 12h-loop fallback trigger for the debounced confirmed-hypotheses
            # rebuild (spec invariant 4). Bound method; only invoked at run time.
            pending_rebuild_hook=self.run_pending_rebuild_if_due,
            confusion_replay_hook=self.replay_confusion_dialogue_attributions,
        )
        self._profile_consolidator: ProfileConsolidator | None = None
        if profile_consolidation_enabled:
            self._profile_consolidator = ProfileConsolidator(
                memory=memory,
                llm_service=self._llm_service,
                embedding_service=embedding_service,
                data_dir=data_dir,
                min_interval_seconds=profile_consolidation_interval_hours * 3600,
                like_target_upper=profile_consolidation_like_target_upper,
                like_target_soft=profile_consolidation_like_target_soft,
                archive_enabled=profile_consolidation_archive_enabled,
                database=database,
            )
        self._pipeline = ProfileUpdatePipeline(
            memory=memory,
            preference_analyzer=self._preference_analyzer,
            profile_builder=self._profile_builder,
            speculator=self._speculator,
            avoidance_speculator=self._avoidance_speculator,
            embedding_service=embedding_service,
            cognition_cycle=self._cognition_cycle,
            speculator_idle_interval_minutes=speculator_idle_interval_minutes,
            profile_consolidator=self._profile_consolidator,
            unified_interest_line=self._unified_interest_line,
            feedback_batch_threshold=self._feedback_batch_threshold,
        )
        # Detached dislike writeback from manual edits, feedback batches, and
        # dialogue learning. The purge runs an LLM+embedding recall that must
        # not block the interactive response, so keep a strong task reference
        # and expose a deterministic wait hook for tests / shutdown.
        self._background_edit_tasks: set[asyncio.Task[Any]] = set()
        self._init_cognition_context: dict[str, object] = {}
        # Phase 0 audit ledger. Best-effort observer over profile write points;
        # a ledger failure is logged at WARNING and never blocks a write. Resolve
        # the database from the explicit arg or the memory manager's handle.
        self._ledger_database = database if database is not None else _memory_database(memory)
        self._ledger = ProfileLedger(self._ledger_database)
        # Deep-line consolidation: one-time migration of any persisted VALUES/CORE
        # pipeline buffer signals into awareness notes, then seal the deep buffers
        # (P1 retired). Idempotent (marker + content-hash dedup) and best-effort —
        # a failure never blocks engine construction. Runs before the first
        # pipeline save so the raw deep-buffer keys are still on disk to read.
        if data_dir is not None:
            try:
                migrate_pipeline_deep_buffers(data_dir, memory, self._ledger)
            except Exception:
                logger.warning("pipeline deep-buffer migration failed", exc_info=True)
        # Confusion state machine over the same database — drives the topic
        # freeze reflex at the dialogue preference write chokepoint (Phase 2).
        self._confusion_manager = ConfusionManager(self._ledger_database, self._ledger)
        self._dialogue_anchor_manager = DialogueAnchorManager(
            data_dir,
            database=self._ledger_database,
            ledger=self._ledger,
        )
        # The API runtime binds its one queue after constructing both sides of
        # the dispatcher cycle. Public submit façades fail closed until then;
        # worker-only apply methods never infer a fallback executor.
        self._dialogue_settlement_queue: DialogueSettlementQueue | None = None
        self._dialogue_mutation_guard: Callable[[], None] | None = None
        # Wire the same ledger into the speculator so promote/confirm/reject
        # write points (D5 #5) land in the same audit trail.
        attach_ledger = getattr(self._speculator, "attach_ledger", None)
        if callable(attach_ledger):
            attach_ledger(self._ledger)
        # Phase 3 posture gate over deep writes (dialogue deep candidates /
        # pipeline VALUES+CORE / soul rebuild). shadow (default) is a zero-delay
        # async side-channel; off is a byte-identical bypass. The pipeline shares
        # the same instance so its VALUES/CORE updater gates through it.
        self._posture_gate = PostureGate(
            mode=posture_gate_mode,
            registry=self._llm_service,
            ledger=self._ledger,
            background_tasks=self._background_edit_tasks,
        )
        set_gate = getattr(self._pipeline, "set_posture_gate", None)
        if callable(set_gate):
            set_gate(self._posture_gate)
        # Unified interest line: hand the pipeline the retired feedback batch's
        # privileges. Wired ONLY when the switch is on, so flipping it off
        # returns the consuming side to today's behaviour too.
        if self._unified_interest_line:
            self._pipeline.set_feedback_hooks(
                FeedbackConsumerHooks(
                    archive_dislikes=self._archive_disliked_topics,
                    after_update=self._after_pipeline_feedback_interest,
                )
            )
        # Held-replay crash recovery (Wave B, r5/R4-1): any held update left in
        # ``replaying`` at construction is a leftover from a previously crashed
        # session — reconcile it to ``applied_unverified`` (never resubmit;
        # prefer under- to double-counting). Fresh replays created later in THIS
        # session are consumed by ``replay_held_updates`` instead. Best-effort.
        try:
            self._confusion_manager.recover_replaying()
        except Exception:
            logger.debug("held-replay crash recovery failed", exc_info=True)

    def set_embedding_service(self, embedding_service: Any) -> None:
        """Attach or update the embedding service after construction.

        Useful when the embedding service is built later than the soul
        engine in the bootstrap order.
        """
        self._embedding_service = embedding_service
        self._preference_analyzer.embedding_service = embedding_service
        self._pipeline.set_embedding_service(embedding_service)
        if self._profile_consolidator is not None:
            self._profile_consolidator.set_embedding_service(embedding_service)

    @property
    def pipeline(self) -> Any:
        """Access the ProfileUpdatePipeline for direct signal ingestion."""
        return self._pipeline

    @property
    def unified_interest_line_enabled(self) -> bool:
        """Whether feedback flows into the pipeline fast line (spec 2026-07-27).

        False (Wave A default) means ``/api/feedback`` must NOT ingest a
        FEEDBACK signal: the legacy feedback batch is still running and would
        double-count the same user action.
        """
        return self._unified_interest_line

    async def analyze_events(
        self,
        events: list[dict[str, Any]],
        *,
        event_chunk_size: int = 0,
        progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
        llm_concurrency: int | None = None,
    ) -> None:
        """Analyze new behavioral events and update all memory layers.

        This is the primary entry point for processing new user behavior.
        Events flow upward through the memory layers, with each layer
        potentially triggering updates in the layers above.

        Args:
            events: List of behavioral event dicts from the collector.
            event_chunk_size: When > 0, split the event list into chunks
                of this size and analyse each chunk in parallel. Useful
                for the init bootstrap where a single max-thinking call
                on ~800 events would block for ~6 minutes.
        """
        import time as _time

        logger.info(
            "analyze_events start: events=%d chunk_size=%d",
            len(events),
            event_chunk_size,
        )
        t0 = _time.monotonic()
        preference_layer = self._memory.get_layer("preference")
        updated_preference = await self._preference_analyzer.analyze_events(
            events=events,
            existing_preference=preference_layer.data,
            event_chunk_size=event_chunk_size,
            progress_callback=progress_callback,
            llm_concurrency=llm_concurrency,
        )
        init_cognition = updated_preference.pop(INIT_COGNITION_CONTEXT_KEY, None)
        self._init_cognition_context = init_cognition if isinstance(init_cognition, dict) else {}
        self._persist_init_cognition_drafts(self._init_cognition_context)
        # Ledger write point D5 #7: full-preference (re)build from raw events —
        # the init bootstrap and any full-events re-analysis both land here.
        existing_preference = dict(preference_layer.data)
        # Topic-lifecycle (Phase 4): carry lifecycle metadata forward and count
        # this analysis as evidence (new topics enter trial; sustained/dormant
        # topics transition). Best-effort — never breaks the analysis path.
        self._apply_topic_lifecycle_evidence(existing_preference, updated_preference)
        with self._ledger.action(
            write_point="init_preference_build",
            source="init",
            before=existing_preference,
            source_refs=[f"events:{len(events)}"],
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)
        logger.info(
            "analyze_events done: events=%d elapsed=%.1fs",
            len(events),
            _time.monotonic() - t0,
        )

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> OnionProfile:
        """Build an initial soul profile from historical data.

        Used on first run to bootstrap the user understanding model
        from existing Bilibili watch history, favorites, etc.

        Args:
            history: Historical data from Bilibili API.

        Returns:
            Initial OnionProfile.
        """
        import time as _time

        logger.info("build_initial_profile start: history=%d items", len(history))
        t0 = _time.monotonic()
        preference_layer = self._memory.get_layer("preference").data
        awareness_notes = [awareness_note_to_dict(item) for item in self._load_awareness_notes()]
        active_insights = [insight_hypothesis_to_dict(item) for item in self._load_insights()]
        # Init drafts are persisted by ``_persist_init_cognition_drafts``, so the
        # two loads above normally already contain them. The in-memory context is
        # still appended because that persistence is best-effort — but dedup by
        # normalized text, or the profile builder would weigh every init draft
        # twice and read it as two independent observations.
        self._extend_without_duplicates(
            awareness_notes, self._init_awareness_context(), "observation"
        )
        self._extend_without_duplicates(active_insights, self._init_insight_context(), "hypothesis")
        legacy_profile = await self._profile_builder.build(
            history=history,
            preference=preference_layer,
            awareness_notes=awareness_notes,
            active_insights=active_insights,
        )
        logger.info(
            "build_initial_profile: legacy profile built in %.1fs",
            _time.monotonic() - t0,
        )
        profile = OnionProfile.from_legacy(legacy_profile)
        profile.populate_from_flat_preference(preference_layer)
        soul_layer = self._memory.get_layer("soul")
        # Ledger write point (extra, discovered during Phase 0 — clist item 7
        # "init 全量建像" also covers the soul-layer bootstrap write here).
        existing_soul = dict(soul_layer.data)
        with self._ledger.action(
            write_point="init_soul_build",
            source="init",
            before=existing_soul,
            source_refs=[f"history:{len(history)}"],
        ) as _entry:
            soul_layer.data.clear()
            soul_layer.data.update(profile.to_dict())
            soul_layer.save()
            _entry.after = dict(soul_layer.data)
        self._memory.sync_profile_files(profile)
        self._init_cognition_context = {}
        logger.info(
            "build_initial_profile done: total_elapsed=%.1fs",
            _time.monotonic() - t0,
        )

        # This return is the strict profile-commit barrier for guided init.
        # Initial interest/avoidance probes are intentionally scheduled by
        # RuntimeContext.restart_background_tasks *after* the first serviceable
        # content pool is attempted. Keeping them out of this method prevents a
        # non-essential maintenance task from extending or deadlocking the
        # load-bearing profile stage.

        return profile

    def _extend_without_duplicates(
        self,
        existing: list[dict[str, object]],
        extra: list[dict[str, object]],
        field: str,
    ) -> None:
        """Append ``extra`` to ``existing``, skipping entries already present."""
        seen = {
            key
            for key in (self._normalize_context_text(str(item.get(field, ""))) for item in existing)
            if key
        }
        for item in extra:
            key = self._normalize_context_text(str(item.get(field, "")))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            existing.append(item)

    def _init_awareness_context(self) -> list[dict[str, object]]:
        raw_items = self._init_cognition_context.get("awareness")
        items = raw_items if isinstance(raw_items, list) else []
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            observation = str(raw.get("observation", "")).strip()
            key = self._normalize_context_text(observation)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "date": str(raw.get("date") or "init"),
                    "observation": observation,
                    "trend": str(raw.get("trend", "")).strip(),
                    "emotion_guess": str(raw.get("emotion_guess", "")).strip(),
                }
            )
        return result

    def _init_insight_context(self) -> list[dict[str, object]]:
        raw_items = self._init_cognition_context.get("insights")
        items = raw_items if isinstance(raw_items, list) else []
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            hypothesis = str(raw.get("hypothesis", "")).strip()
            key = self._normalize_context_text(hypothesis)
            if not key or key in seen:
                continue
            seen.add(key)
            evidence = raw.get("evidence")
            result.append(
                {
                    "hypothesis": hypothesis,
                    "evidence": [
                        str(item).strip()
                        for item in (evidence if isinstance(evidence, list) else [])
                        if str(item).strip()
                    ][:5],
                    "confidence": self._clamp_confidence(raw.get("confidence", 0.5)),
                    "validated": bool(raw.get("validated", False)),
                    "created_at": str(raw.get("created_at") or "init"),
                }
            )
        return result

    @staticmethod
    def _normalize_context_text(value: str) -> str:
        return " ".join(value.strip().lower().split())

    @staticmethod
    def _clamp_confidence(value: object) -> float:
        if not isinstance(value, str | int | float) or isinstance(value, bool):
            return 0.5
        try:
            number = float(value)
        except ValueError:
            return 0.5
        return max(0.0, min(1.0, number))

    def is_profile_ready(self) -> bool:
        """Cheap, non-raising check for whether a soul profile exists.

        Background-task consumers call this first to avoid using
        ``SoulProfileNotInitializedError`` as flow control during the
        ~7-minute init window — which would otherwise produce ERROR-level
        traces for every classify / awareness / speculator tick that
        runs before the profile lands.
        """
        try:
            return bool(self._memory.get_layer("soul").data)
        except Exception:
            return False

    async def get_profile(self) -> OnionProfile:
        """Get the current *effective* soul profile (AI profile ⊕ user overrides).

        Returns:
            The OnionProfile from the soul memory layer with user overrides
            merged on top. Active speculative interests are attached as
            ``_active_speculations``.
        """
        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            raise SoulProfileNotInitializedError("Soul profile has not been initialized yet.")
        profile = OnionProfile.from_dict(soul_data)
        profile = apply_overrides(profile, self._memory.load_profile_overrides())
        # Flat preference writeback can land before the asynchronous profile
        # rebuild. Expose the authoritative dislike snapshot immediately so
        # serve-time and recommendation-output filters cannot observe a stale
        # profile in that window.
        effective_dislikes = self.get_effective_disliked_topics()
        existing_dislikes = {
            str(domain.domain).strip().casefold() for domain in profile.interest.dislikes
        }
        if effective_dislikes:
            from openbiliclaw.soul.profile import InterestDomain

            for topic in effective_dislikes:
                text = str(topic).strip()
                key = text.casefold()
                if text and key not in existing_dislikes:
                    profile.interest.dislikes.append(InterestDomain(domain=text, weight=0.9))
                    existing_dislikes.add(key)
        # Attach active speculations so downstream consumers (Discovery) can use them
        active_specs = self._speculator.get_active_speculations()
        if active_specs:
            profile._active_speculations = active_specs  # type: ignore[attr-defined]
        return profile

    async def get_raw_profile(self) -> OnionProfile:
        """Get the AI-generated profile WITHOUT user overrides.

        Used by the edit-state endpoint and drift detection so the UI can show
        the AI's current suggestion alongside the user's pinned value.
        """
        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            raise SoulProfileNotInitializedError("Soul profile has not been initialized yet.")
        return OnionProfile.from_dict(soul_data)

    def get_overrides(self) -> ProfileOverrides:
        """Return the current user-authored profile overrides."""
        return self._memory.load_profile_overrides()

    def get_effective_disliked_topics(self) -> list[str]:
        """Effective dislike terms for hard filters.

        Soul-side dislikes are taken from the EFFECTIVE profile (``apply_overrides``)
        so overlay edits at *every* granularity reflect here — domain add/remove
        AND per-domain specific add/remove. Flat ``preference.disliked_topics``
        (which lives outside the soul layer) is unioned in, but suppressed by any
        overlay dislike removal (domain- or specific-level) so a user-removed term
        is not re-added by the raw preference layer (F6).
        """
        overrides = self._memory.load_profile_overrides()
        terms: list[str] = []
        soul_data = self._memory.get_layer("soul").data
        if soul_data:
            effective = apply_overrides(OnionProfile.from_dict(soul_data), overrides)
            for domain in effective.interest.dislikes:
                terms.append(domain.domain)
                terms.extend(spec.name for spec in domain.specifics)
        remove_keys: set[str] = set()
        dislikes_edit = overrides.interest_edits.get("dislikes")
        if dislikes_edit is not None:
            removals = list(dislikes_edit.remove_domains)
            for spec_edit in dislikes_edit.specific_edits.values():
                removals.extend(spec_edit.remove)
            remove_keys = {item.strip().lower() for item in removals if item.strip()}
        preference_data = self._memory.get_layer("preference").data
        if isinstance(preference_data, dict):
            raw_topics = preference_data.get("disliked_topics")
            if isinstance(raw_topics, list):
                terms.extend(str(topic) for topic in raw_topics)
        result: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.strip().lower()
            if not key or key in seen or key in remove_keys:
                continue
            seen.add(key)
            result.append(term)
        return result

    async def apply_user_edit(
        self,
        *,
        target: str,
        op: str,
        value: object = None,
        parent: str = "",
        weight: float | None = None,
        database: Any | None = None,
        embedding_service: Any | None = None,
        llm_service: Any | None = None,
    ) -> dict[str, object]:
        """Apply one deterministic user edit to the profile overrides.

        Pipeline: snapshot effective dislikes → fold the edit into the
        overrides (validated; raises ``ProfileEditError`` on bad input) →
        persist → if the edit added *new* effective dislikes, purge matching
        already-pooled content (diff, not the raw value) → sync the matching
        speculator → record a manual cognition update → refresh the
        human-readable mirror (re-applies the overlay) and notify both
        surfaces. Returns ``{ok, target, op}``.
        """
        before = set(self.get_effective_disliked_topics())

        overrides = self._memory.load_profile_overrides()
        updated, _ = apply_edit(
            overrides, target=target, op=op, value=value, parent=parent, weight=weight
        )
        updated.updated_at = datetime.now().isoformat()
        self._memory.save_profile_overrides(updated)

        after = set(self.get_effective_disliked_topics())
        newly_added = sorted(after - before)

        self._sync_speculators_for_edit(target=target, op=op, value=value)
        self._record_manual_cognition(target=target, op=op, value=value)

        if self._memory.get_layer("soul").data:
            self._memory.sync_profile_files(await self.get_raw_profile())

        # The dislike pool purge does an embedding recall + LLM classification
        # that can take tens of seconds. It is a best-effort cleanup of
        # already-pooled content and MUST NOT block the edit response — doing so
        # makes the UI hang for the whole call and the new dislike appears "not
        # saved". Run it detached; the override itself is already persisted.
        if newly_added:
            self._schedule_dislike_purge(
                newly_added=newly_added,
                all_dislikes=sorted(after),
                database=database,
                embedding_service=embedding_service,
                llm_service=llm_service,
            )

        return {"ok": True, "target": target, "op": op}

    def _schedule_dislike_purge(self, **kwargs: Any) -> None:
        """Run a learned-dislike pool purge outside the interactive request.

        Every caller runs inside an event loop. Failures are swallowed inside
        ``_purge_for_new_dislikes``; the done-callback only drops the tracking
        reference.
        """
        task = asyncio.ensure_future(self._purge_for_new_dislikes(**kwargs))
        self._background_edit_tasks.add(task)
        task.add_done_callback(self._background_edit_tasks.discard)

    async def wait_for_pending_edits(self) -> None:
        """Await detached dislike-purge work from any learning path.

        Used by tests and graceful shutdown so the background purge can finish
        deterministically. No-op when nothing is pending.
        """
        tasks = list(self._background_edit_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _purge_for_new_dislikes(
        self,
        *,
        newly_added: list[str],
        all_dislikes: list[str],
        database: Any | None,
        embedding_service: Any | None,
        llm_service: Any | None,
    ) -> None:
        """Reuse the confirmed-avoidance purge for a newly learned dislike."""
        db = database if database is not None else getattr(self._memory, "_database", None)
        if db is None:
            logger.info("skip learned-dislike pool purge: no database available")
            return
        embedding = embedding_service if embedding_service is not None else self._embedding_service
        llm = llm_service if llm_service is not None else self._llm_service
        try:
            from openbiliclaw.soul.dislike_writeback import purge_pool_for_new_dislikes

            await purge_pool_for_new_dislikes(
                database=db,
                embedding_service=embedding,
                llm_service=llm,
                newly_added=newly_added,
                all_dislikes=all_dislikes,
            )
        except Exception:
            logger.exception("learned-dislike pool purge failed")

    def _apply_topic_lifecycle_evidence(
        self,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
    ) -> None:
        """Overlay topic-lifecycle metadata onto a freshly analysed preference.

        Carries lifecycle fields forward from ``existing_preference`` and counts
        this analysis only for topics whose ``last_seen`` changed (new → trial;
        sustained → active; dormant → active). Retained topics are not fresh
        evidence. Best-effort: any failure is logged at DEBUG and never breaks
        the analysis path. Each transition is recorded to the ledger (write
        point ``topic_lifecycle``).
        """
        try:
            from openbiliclaw.soul.topic_lifecycle import apply_evidence, changed_interest_keys

            existing_interests = [
                item for item in existing_preference.get("interests", []) if isinstance(item, dict)
            ]
            updated_interests = updated_preference.get("interests")
            if not isinstance(updated_interests, list):
                return
            evidence_keys = changed_interest_keys(existing_interests, updated_interests)
            merged, transitions = apply_evidence(
                existing_interests,
                updated_interests,
                evidence_keys=evidence_keys,
            )
            updated_preference["interests"] = merged
            for tr in transitions:
                self._ledger.record(
                    write_point="topic_lifecycle",
                    source="evidence",
                    before={"topic": tr.name, "state": tr.from_state},
                    after={"topic": tr.name, "state": tr.to_state},
                    source_refs=[f"reason:{tr.reason}"],
                )
        except Exception:
            logger.debug("topic lifecycle evidence overlay failed", exc_info=True)

    def _archive_disliked_topics(
        self,
        updated_preference: dict[str, Any],
        disliked_topics: list[str],
    ) -> None:
        """Archive interests matching newly disliked topics (归档+避雷).

        The interest is archived, not deleted — it survives for audit/revert but
        stops competing for prompt slots. Best-effort; ledgers each transition.
        """
        try:
            from openbiliclaw.soul.topic_lifecycle import archive_topics

            interests = updated_preference.get("interests")
            if not isinstance(interests, list):
                return
            archived, transitions = archive_topics(interests, disliked_topics)
            updated_preference["interests"] = archived
            for tr in transitions:
                self._ledger.record(
                    write_point="topic_lifecycle",
                    source="dislike",
                    before={"topic": tr.name, "state": tr.from_state},
                    after={"topic": tr.name, "state": tr.to_state},
                    source_refs=[f"reason:{tr.reason}"],
                )
        except Exception:
            logger.debug("topic lifecycle dislike-archive failed", exc_info=True)

    def _sync_speculators_for_edit(self, *, target: str, op: str, value: object) -> None:
        """Keep the interest / avoidance speculators consistent with the edit.

        like add/remove → interest speculator confirm/reject; dislike
        add/remove → avoidance speculator confirm/reject. Defensive via
        getattr so older speculator doubles don't break edits.
        """
        if not isinstance(value, str) or not value.strip():
            return
        domain = value.strip()
        speculator: Any = None
        method_name = ""
        if target == "likes":
            speculator = self._speculator
            if op == "add":
                method_name = "user_confirm_speculation"
            elif op == "remove":
                method_name = "user_reject_speculation"
        elif target == "dislikes":
            speculator = self._avoidance_speculator
            if op == "add":
                method_name = "user_confirm_avoidance"
            elif op == "remove":
                method_name = "user_reject_avoidance"
        if not method_name:
            return
        fn = getattr(speculator, method_name, None)
        if callable(fn):
            try:
                fn(domain)
            except Exception:
                logger.debug("speculator sync failed: %s %s", target, op, exc_info=True)

    def _record_manual_cognition(self, *, target: str, op: str, value: object) -> None:
        summary = self._manual_edit_summary(target=target, op=op, value=value)
        if not summary:
            return
        updates = self._memory.load_cognition_updates()
        updates.insert(
            0,
            {
                "id": f"cognition-{uuid4()}",
                "kind": "manual_edit",
                "summary": summary,
                "impact": "",
                "reasoning": "",
                "evidence": "",
                "context_line": "你手动编辑了画像",
                "confidence": 1.0,
                "created_at": datetime.now().isoformat(),
                "source": "manual",
                "source_label": "手动编辑",
                "expand_hint": "summary_only",
                "notified": False,
            },
        )
        self._memory.save_cognition_updates(updates)

    @staticmethod
    def _manual_edit_summary(*, target: str, op: str, value: object) -> str:
        label = _MANUAL_EDIT_LABELS.get(target, target)
        text = value.strip() if isinstance(value, str) else ""
        if op == "add" and text:
            return f"你把「{text}」加进了{label}。"
        if op == "remove" and text:
            return f"你把「{text}」从{label}移除了。"
        if op == "set":
            return f"你改写了{label}。"
        if op == "reset":
            return f"你恢复了{label}的 AI 建议。"
        return f"你编辑了{label}。"

    async def update_from_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Update soul understanding based on explicit user feedback on a hypothesis.

        Confirm/reject feedback on a specific insight hypothesis calibrates that
        hypothesis: a confirm pins ``validated=True`` and raises confidence to at
        least 0.75; a reject sets ``validated=False`` and caps confidence at 0.35
        (the "soft invalidation" — the hypothesis is down-weighted in delight
        scoring rather than deleted). The feedback is also logged as an event.

        Wired to ``POST /api/insights/feedback`` so the UI's insight cards can
        drive this loop.

        Args:
            feedback: ``{"hypothesis": str, "signal": str}``. ``signal`` is one
                of confirm/like/support (positive) or reject/dislike/deny.

        Returns:
            A result dict describing whether a hypothesis matched and its
            post-update state — consumed by the API endpoint.
        """
        logger.info("Updating soul from feedback...")
        await self._memory.propagate_event(
            {
                "event_type": "feedback",
                "title": str(feedback.get("hypothesis", "")),
                "metadata": feedback,
            }
        )
        result = await self.apply_feedback_object(feedback)
        await self.mark_feedback_rebuild(feedback, result)
        return result

    async def apply_feedback_object(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Apply only the idempotent hypothesis-object feedback effect.

        Durable card settlement owns event receipt and rebuild-marker effects
        separately, so the single settlement worker calls this method directly.
        ``update_from_feedback`` remains the compatibility facade that executes
        all three effects in the historical order.
        """
        hypotheses = self._load_insights()
        target = self._normalize_text(str(feedback.get("hypothesis", "")))
        signal = str(feedback.get("signal", "")).strip().lower()
        result: dict[str, Any] = {
            "matched": False,
            "hypothesis": str(feedback.get("hypothesis", "")),
            "signal": signal,
            "validated": False,
            "confidence": 0.0,
        }
        changed = False
        for item in hypotheses:
            if self._normalize_text(item.hypothesis) != target:
                continue
            previous_validated = item.validated
            previous_confidence = item.confidence
            if signal in {"confirm", "like", "support"}:
                item.validated = True
                item.confidence = min(1.0, round(max(item.confidence, 0.75), 4))
                item.user_verdict = "confirmed"
            elif signal in {"reject", "dislike", "deny"}:
                item.validated = False
                item.confidence = max(0.0, round(min(item.confidence, 0.35), 4))
                # Record that the user ruled on this, not just the low score:
                # a later insight pass must not talk it back up (see
                # InsightAnalyzer._merge_confidence).
                item.user_verdict = "rejected"
            changed = item.validated != previous_validated or item.confidence != previous_confidence
            result["matched"] = True
            result["hypothesis"] = item.hypothesis
            result["validated"] = item.validated
            result["confidence"] = item.confidence
            break
        if result["matched"] and changed:
            self._save_insights(hypotheses)
            # The insight layer is the source of truth, but get_profile()
            # (UI profile-summary + delight scoring) reads the windowed
            # ``active_insights`` snapshot cached on the soul layer. Without
            # mirroring the calibration there, a confirm/reject wouldn't take
            # visible or recommendation effect until the next 12h cognition
            # sync. Patch the snapshot in place so the change is immediate.
            self._sync_insight_to_soul_snapshot(
                target_normalized=target,
                validated=bool(result["validated"]),
                confidence=float(result["confidence"]),
            )
        return result

    async def mark_feedback_rebuild(
        self,
        feedback: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Apply the idempotent rebuild-marker segment for matched feedback."""
        signal = str(feedback.get("signal", "")).strip().lower()
        if not bool(result.get("matched", False)) or signal not in {
            "confirm",
            "like",
            "support",
            "reject",
            "dislike",
            "deny",
        }:
            return
        await self._mark_rebuild_pending(
            [f"insight_feedback:{signal}:{str(result.get('hypothesis', ''))[:60]}"]
        )

    def feedback_result(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Read the current hypothesis state without replaying a completed segment."""
        target = self._normalize_text(str(feedback.get("hypothesis", "")))
        signal = str(feedback.get("signal", "")).strip().lower()
        result: dict[str, Any] = {
            "matched": False,
            "hypothesis": str(feedback.get("hypothesis", "")),
            "signal": signal,
            "validated": False,
            "confidence": 0.0,
        }
        for item in self._load_insights():
            if self._normalize_text(item.hypothesis) != target:
                continue
            result.update(
                {
                    "matched": True,
                    "hypothesis": item.hypothesis,
                    "validated": item.validated,
                    "confidence": item.confidence,
                }
            )
            break
        return result

    def bind_dialogue_settlement_queue(self, queue: DialogueSettlementQueue) -> None:
        """Bind the runtime-owned single queue used by public submit façades."""
        current = self._dialogue_settlement_queue
        if current is not None and current is not queue:
            raise RuntimeError("SoulEngine already has a dialogue settlement queue")
        self._dialogue_settlement_queue = queue
        guard = queue.require_dialogue_settlement_worker
        self._dialogue_mutation_guard = guard
        self._dialogue_anchor_manager.install_mutation_guard(guard)
        self._confusion_manager.install_mutation_guard(guard)

    def _require_dialogue_settlement_worker(self) -> None:
        guard = self._dialogue_mutation_guard
        if guard is None:
            # Low-level queue tests construct the dispatcher before binding an
            # engine façade. Production runtimes always install their own
            # per-context guard through ``bind_dialogue_settlement_queue``.
            from .dialogue_settlement_guard import require_dialogue_settlement_worker

            require_dialogue_settlement_worker()
            return
        guard()

    async def submit_hypothesis_settlement(
        self,
        *,
        ref: str,
        hypothesis: str,
        requested_verdict: str,
        turn_id: str,
        source: str,
        derived: list[dict[str, object]] | None = None,
    ) -> dict[str, Any]:
        """Submit one hypothesis settlement with its admission anchor frozen."""
        normalized_ref = ref.strip()
        queue = self._require_dialogue_settlement_queue()
        completion = await queue.submit_and_wait(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {
                "ref": normalized_ref,
                "hypothesis": hypothesis,
                "requested_verdict": requested_verdict,
                "turn_id": turn_id,
                "source": source,
                "derived": list(derived or []),
                "target_kind": "hypothesis",
                "target_ref": normalized_ref,
            },
        )
        if completion.settlement is not None:
            return dict(completion.settlement)
        return {"outcome": completion.outcome}

    async def submit_confusion_answer_settlement(
        self,
        *,
        ref: str,
        confusion_id: int,
        interpretation: str,
        note: str,
        turn_id: str,
        source: str,
    ) -> dict[str, Any]:
        """Submit one confusion answer with its admission anchor frozen."""
        normalized_ref = ref.strip()
        queue = self._require_dialogue_settlement_queue()
        completion = await queue.submit_and_wait(
            DialogueJobKind.SETTLE_CONFUSION,
            {
                "ref": normalized_ref,
                "confusion_id": confusion_id,
                "interpretation": interpretation,
                "note": note,
                "turn_id": turn_id,
                "source": source,
                "target_kind": "confusion",
                "target_ref": normalized_ref,
            },
        )
        if completion.settlement is not None:
            return dict(completion.settlement)
        return {"outcome": completion.outcome}

    async def submit_confusion_settlement(
        self,
        *,
        ref: str,
        requested_verdict: str,
        note: str,
        turn_id: str,
        source: str,
    ) -> dict[str, Any]:
        """Submit one confirm/reject confusion action to the single queue."""
        try:
            confusion_id = int(ref)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Confusion settlement ref must be an integer: {ref!r}") from exc
        interpretation = {
            "confirm": "real_interest",
            "confirmed": "real_interest",
            "reject": "proxy_behavior",
            "rejected": "proxy_behavior",
        }.get(requested_verdict.strip().lower())
        if interpretation is None:
            raise ValueError(f"Unsupported confusion settlement: {requested_verdict!r}")
        return await self.submit_confusion_answer_settlement(
            ref=ref,
            confusion_id=confusion_id,
            interpretation=interpretation,
            note=note,
            turn_id=turn_id,
            source=source,
        )

    def _require_dialogue_settlement_queue(self) -> DialogueSettlementQueue:
        queue = self._dialogue_settlement_queue
        if queue is None:
            raise RuntimeError("Dialogue settlement queue is not bound")
        return queue

    async def _apply_hypothesis_settlement(
        self,
        *,
        ref: str,
        hypothesis: str,
        requested_verdict: str,
        turn_id: str,
        source: str,
        anchor_snapshot: AnchorAdmissionSnapshot,
        derived: list[dict[str, object]] | None = None,
        provenance: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Apply one admitted hypothesis settlement in the actual worker."""
        return await self._apply_dialogue_settlement(
            kind="hypothesis",
            ref=ref,
            title=hypothesis,
            requested_verdict=requested_verdict,
            turn_id=turn_id,
            source=source,
            derived=derived,
            anchor_snapshot=anchor_snapshot,
            provenance=provenance,
        )

    async def _apply_confusion_answer_settlement(
        self,
        *,
        ref: str,
        confusion_id: int,
        interpretation: str,
        note: str,
        turn_id: str,
        source: str,
        anchor_snapshot: AnchorAdmissionSnapshot,
        provenance: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Apply one admitted confusion answer in the actual worker."""
        return await self._apply_dialogue_settlement(
            kind="confusion",
            ref=ref,
            title=f"confusion:{confusion_id}",
            requested_verdict="answer",
            turn_id=turn_id,
            source=source,
            confusion_id=confusion_id,
            interpretation=interpretation,
            note=note,
            anchor_snapshot=anchor_snapshot,
            provenance=provenance,
        )

    async def _apply_confusion_settlement(
        self,
        *,
        ref: str,
        requested_verdict: str,
        note: str,
        turn_id: str,
        source: str,
        anchor_snapshot: AnchorAdmissionSnapshot,
    ) -> dict[str, Any]:
        """Apply one admitted unanchored confusion action in the worker."""
        try:
            confusion_id = int(ref)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Confusion settlement ref must be an integer: {ref!r}") from exc
        interpretation = {
            "confirm": "real_interest",
            "confirmed": "real_interest",
            "reject": "proxy_behavior",
            "rejected": "proxy_behavior",
        }.get(requested_verdict.strip().lower())
        if interpretation is None:
            raise ValueError(f"Unsupported confusion settlement: {requested_verdict!r}")
        return await self._apply_confusion_answer_settlement(
            ref=ref,
            confusion_id=confusion_id,
            interpretation=interpretation,
            note=note,
            turn_id=turn_id,
            source=source,
            anchor_snapshot=anchor_snapshot,
        )

    async def _apply_speculation_settlement(
        self,
        *,
        ref: str,
        requested_verdict: str,
        turn_id: str,
        source: str,
    ) -> dict[str, Any]:
        """Apply one ordinary-chat speculation settlement in the worker."""
        return await self._apply_dialogue_settlement(
            kind="speculation",
            ref=ref,
            title=ref,
            requested_verdict=requested_verdict,
            turn_id=turn_id,
            source=source,
            anchor_snapshot=ANCHOR_NOT_APPLICABLE,
        )

    async def _apply_card_reconcile(self, *, ref: str) -> dict[str, Any]:
        """Replay publication only for one already-applied winner receipt."""
        self._require_dialogue_settlement_worker()
        database = self._ledger_database
        if database is None:
            raise RuntimeError("Dialogue settlement store is not ready")
        settlement = database.get_card_settlement(ref.strip())
        if not isinstance(settlement, dict):
            return {
                "outcome": "not_found",
                "settlement_ref": ref.strip(),
            }
        if int(settlement.get("applied", 0)) != 1:
            return self._dialogue_settlement_response(
                outcome="processing",
                settlement=settlement,
            )
        raw_payload = settlement.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        result = self._reconcile_applied_dialogue_settlement_publication(
            ref=ref.strip(),
            settlement=settlement,
            payload=payload,
        )
        return self._dialogue_settlement_response(
            outcome="already_settled",
            settlement=settlement,
            result=result,
        )

    async def _apply_dialogue_settlement(
        self,
        *,
        kind: str,
        ref: str,
        title: str,
        requested_verdict: str,
        turn_id: str,
        source: str,
        derived: list[dict[str, object]] | None = None,
        confusion_id: int = 0,
        interpretation: str = "",
        note: str = "",
        anchor_snapshot: AnchorAdmissionSnapshot,
        provenance: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        """Apply one frozen winner through idempotent effects in the worker."""
        self._require_dialogue_settlement_worker()
        database = self._ledger_database
        required_methods = (
            "try_create_card_settlement",
            "get_card_settlement",
            "record_card_settlement_event_once",
            "complete_card_settlement",
            "project_applied_card_settlement",
        )
        if database is None or any(
            not callable(getattr(database, name, None)) for name in required_methods
        ):
            raise RuntimeError("Dialogue settlement store is not ready")

        normalized_kind = kind.strip().lower()
        normalized_request = requested_verdict.strip().lower()
        normalized_ref = ref.strip()
        normalized_turn_id = turn_id.strip()
        if not normalized_ref:
            raise ValueError("Dialogue settlement ref is required")
        settlement = database.get_card_settlement(normalized_ref)
        anchor_generation = 0
        if settlement is None:
            incoming_anchor_error = self._validate_dialogue_settlement_anchor(
                kind=normalized_kind,
                ref=normalized_ref,
                snapshot=anchor_snapshot,
            )
            if incoming_anchor_error:
                return self._dialogue_settlement_anchor_error(
                    outcome=incoming_anchor_error,
                    ref=normalized_ref,
                )
            anchor_generation = self._dialogue_settlement_anchor_generation(
                kind=normalized_kind,
                ref=normalized_ref,
                snapshot=anchor_snapshot,
            )
        if normalized_kind == "hypothesis":
            verdict = {
                "confirm": "confirmed",
                "confirmed": "confirmed",
                "support": "confirmed",
                "reject": "rejected",
                "rejected": "rejected",
                "contradict": "rejected",
                "revise": "revised",
                "revised": "revised",
            }.get(normalized_request)
            if verdict is None:
                raise ValueError(f"Unsupported hypothesis settlement: {requested_verdict!r}")
            winning_payload: dict[str, object] = {
                "kind": "hypothesis",
                "title": title.strip(),
                "action": verdict,
                "derived": list(derived or []),
                "anchor_generation": anchor_generation,
                "source": source,
            }
        elif normalized_kind == "confusion":
            normalized_interpretation = interpretation.strip().lower()
            if (
                normalized_request != "answer"
                or confusion_id <= 0
                or normalized_interpretation not in _CONFUSION_SETTLEMENT_RESOLUTIONS
            ):
                raise ValueError("Unsupported confusion answer settlement")
            verdict = f"answer:{normalized_interpretation}"
            winning_payload = {
                "kind": "confusion",
                "title": title.strip(),
                "action": "answer",
                "confusion_id": confusion_id,
                "interpretation": normalized_interpretation,
                "note": note,
                "anchor_generation": anchor_generation,
                "source": source,
            }
        elif normalized_kind == "speculation":
            verdict = {
                "confirm": "confirmed",
                "confirmed": "confirmed",
                "reject": "rejected",
                "rejected": "rejected",
            }.get(normalized_request)
            if verdict is None:
                raise ValueError(f"Unsupported speculation settlement: {requested_verdict!r}")
            winning_payload = {
                "kind": "speculation",
                "title": title.strip(),
                "action": verdict,
                "source": source,
            }
        else:
            raise ValueError(f"Unsupported dialogue settlement kind: {kind!r}")

        if provenance:
            for key in (
                "source_turn_id",
                "source_reply_to_turn_id",
                "source_context_digest",
                "source_binding_mode",
            ):
                value = str(provenance.get(key, "")).strip()
                if value:
                    winning_payload[key] = value

        if settlement is None:
            database.try_create_card_settlement(
                ref=normalized_ref,
                verdict=verdict,
                turn_id=normalized_turn_id,
                payload=winning_payload,
            )
            settlement = database.get_card_settlement(normalized_ref)
        if not isinstance(settlement, dict):
            raise RuntimeError("Dialogue settlement arbitration row disappeared")
        stored_payload = settlement.get("payload")
        payload = dict(stored_payload) if isinstance(stored_payload, dict) else {}
        stored_kind = str(payload.get("kind", normalized_kind)).strip().lower()
        stored_title = str(payload.get("title", title)).strip()
        stored_source = str(payload.get("source", source)).strip() or source
        stored_verdict = str(settlement.get("verdict", "")).strip().lower()
        if int(settlement.get("applied", 0)) == 1:
            applied_result = self._reconcile_applied_dialogue_settlement_publication(
                ref=normalized_ref,
                settlement=settlement,
                payload=payload,
            )
            return self._dialogue_settlement_response(
                outcome="already_settled",
                settlement=settlement,
                result=applied_result,
            )

        stored_anchor_snapshot = self._stored_dialogue_settlement_anchor_snapshot(
            kind=stored_kind,
            ref=normalized_ref,
            payload=payload,
        )
        stored_anchor_error = self._validate_dialogue_settlement_anchor(
            kind=stored_kind,
            ref=normalized_ref,
            snapshot=stored_anchor_snapshot,
        )
        if stored_anchor_error:
            return self._dialogue_settlement_anchor_error(
                outcome=stored_anchor_error,
                ref=normalized_ref,
            )

        event_type = {
            "hypothesis": "feedback",
            "confusion": "confusion_settlement",
            "speculation": "speculation_settlement",
        }.get(stored_kind)
        if event_type is None:
            raise RuntimeError(f"Stored dialogue settlement kind is invalid: {stored_kind!r}")
        event_metadata: dict[str, object] = {
            "settlement_ref": normalized_ref,
            "settlement_kind": stored_kind,
            "settlement_verdict": stored_verdict,
            "turn_id": str(settlement.get("turn_id", normalized_turn_id)),
            "source": stored_source,
        }
        for payload_key, event_key in (
            ("source_turn_id", "source_turn_id"),
            ("source_reply_to_turn_id", "source_reply_to_turn_id"),
            ("source_context_digest", "source_context_digest"),
            ("source_binding_mode", "binding_mode"),
        ):
            value = str(payload.get(payload_key, "")).strip()
            if value:
                event_metadata[event_key] = value
        if stored_kind == "hypothesis":
            event_metadata.update(self._settlement_feedback(stored_title, stored_verdict))
        elif stored_kind == "confusion":
            event_metadata.update(
                {
                    "confusion_id": payload.get("confusion_id", 0),
                    "interpretation": payload.get("interpretation", ""),
                }
            )
        else:
            event_metadata["domain"] = stored_title
        event = {
            "event_type": event_type,
            "title": stored_title,
            "metadata": event_metadata,
        }
        database.record_card_settlement_event_once(
            ref=normalized_ref,
            event=event,
        )
        self._dialogue_settlement_checkpoint("after_event", normalized_ref)
        result = await self._apply_dialogue_settlement_object(
            settlement=settlement,
            payload=payload,
        )
        self._dialogue_settlement_checkpoint("after_object", normalized_ref)
        if result is None:
            return self._dialogue_settlement_response(
                outcome="processing",
                settlement=settlement,
            )
        derived_trigger_refs = await self._apply_dialogue_settlement_derived(
            settlement=settlement,
            payload=payload,
        )
        self._dialogue_settlement_checkpoint("after_derived", normalized_ref)
        rebuild_trigger_refs = self._dialogue_settlement_rebuild_trigger_refs(
            settlement=settlement,
            payload=payload,
            result=result,
            derived_trigger_refs=derived_trigger_refs,
        )
        if rebuild_trigger_refs:
            await self._mark_rebuild_pending(rebuild_trigger_refs)
        self._dialogue_settlement_checkpoint("after_rebuild_marker", normalized_ref)
        self._record_dialogue_settlement_ledgers(
            ref=normalized_ref,
            settlement=settlement,
            payload=payload,
            result=result,
        )

        if not bool(database.complete_card_settlement(ref=normalized_ref, result=result)):
            latest = database.get_card_settlement(normalized_ref)
            if isinstance(latest, dict) and int(latest.get("applied", 0)) == 1:
                latest_payload_raw = latest.get("payload")
                latest_payload = (
                    dict(latest_payload_raw) if isinstance(latest_payload_raw, dict) else {}
                )
                latest_result = self._reconcile_applied_dialogue_settlement_publication(
                    ref=normalized_ref,
                    settlement=latest,
                    payload=latest_payload,
                )
                return self._dialogue_settlement_response(
                    outcome="already_settled",
                    settlement=latest,
                    result=latest_result,
                )
            return self._dialogue_settlement_response(
                outcome="processing",
                settlement=latest if isinstance(latest, dict) else settlement,
                result=result,
            )
        latest = database.get_card_settlement(normalized_ref) or settlement
        self._dialogue_settlement_checkpoint(
            "after_applied_before_projection",
            normalized_ref,
        )
        self._project_dialogue_settlement(normalized_ref, latest)
        self._dialogue_settlement_checkpoint("after_projection", normalized_ref)
        self._publish_dialogue_settlement_anchor(normalized_ref, latest)
        self._dialogue_settlement_checkpoint("after_anchor_release", normalized_ref)
        return self._dialogue_settlement_response(
            outcome="applied",
            settlement=latest,
            result=result,
        )

    def _validate_dialogue_settlement_anchor(
        self,
        *,
        kind: str,
        ref: str,
        snapshot: AnchorAdmissionSnapshot,
    ) -> str:
        """Validate the frozen state without ever upgrading it from current."""
        if isinstance(snapshot, AnchorFailed):
            return "anchor_dependency_failed"
        if isinstance(snapshot, AnchorReserved):
            raise RuntimeError("Unresolved anchor reservation reached settlement apply")
        if isinstance(snapshot, AnchorNotApplicable):
            return "" if kind == "speculation" else "stale_anchor"
        if isinstance(snapshot, AnchorPersisted):
            if snapshot.kind != kind or snapshot.ref != ref:
                return "stale_anchor"
            active = self._dialogue_anchor_manager.validate_snapshot(
                snapshot.ref,
                snapshot.generation,
            )
            if active is None or active.kind != snapshot.kind:
                return "stale_anchor"
            return ""
        if snapshot.target_kind != kind or snapshot.target_ref != ref:
            return "stale_anchor"
        return "stale_anchor" if self._dialogue_anchor_manager.current() is not None else ""

    @staticmethod
    def _dialogue_settlement_anchor_generation(
        *,
        kind: str,
        ref: str,
        snapshot: AnchorAdmissionSnapshot,
    ) -> int:
        if isinstance(snapshot, AnchorPersisted):
            if snapshot.kind != kind or snapshot.ref != ref:
                raise ValueError("Persisted settlement anchor target mismatch")
            return snapshot.generation
        if isinstance(snapshot, AnchorAbsent):
            if snapshot.target_kind != kind or snapshot.target_ref != ref:
                raise ValueError("Absent settlement anchor target mismatch")
            return 0
        if isinstance(snapshot, AnchorNotApplicable) and kind == "speculation":
            return 0
        if isinstance(snapshot, AnchorFailed):
            return 0
        raise ValueError("Settlement apply requires a resolved admission anchor")

    @staticmethod
    def _stored_dialogue_settlement_anchor_snapshot(
        *,
        kind: str,
        ref: str,
        payload: dict[str, Any],
    ) -> AnchorAdmissionSnapshot:
        if kind == "speculation":
            return ANCHOR_NOT_APPLICABLE
        try:
            generation = max(0, int(payload.get("anchor_generation", 0)))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Stored settlement anchor generation is invalid") from exc
        if generation > 0:
            return AnchorPersisted(kind=kind, ref=ref, generation=generation)
        return AnchorAbsent(target_kind=kind, target_ref=ref, tombstone_epoch=1)

    @staticmethod
    def _dialogue_settlement_anchor_error(*, outcome: str, ref: str) -> dict[str, Any]:
        return {
            "outcome": outcome,
            "state": "stale",
            "settlement_ref": ref,
            "settlement_verdict": "",
        }

    async def _apply_dialogue_settlement_object(
        self,
        *,
        settlement: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Apply the winner's idempotent object segment."""
        kind = str(payload.get("kind", "hypothesis")).strip().lower()
        verdict = str(settlement.get("verdict", "")).strip().lower()
        title = str(payload.get("title", "")).strip()
        if kind == "hypothesis":
            feedback = self._settlement_feedback(title, verdict)
            return await self.apply_feedback_object(feedback)
        if kind == "speculation":
            state = self._speculator._load_state()
            active = next(
                (item for item in state.active if item.domain.casefold() == title.casefold()),
                None,
            )
            cooldown = next(
                (item for item in state.cooldown if item.domain.casefold() == title.casefold()),
                None,
            )
            if verdict == "confirmed":
                applied = bool(active is not None and active.status == "confirmed")
                if not applied:
                    applied = bool(self._speculator.user_confirm_speculation(title))
                status = "confirmed"
            else:
                applied = cooldown is not None
                if not applied:
                    applied = bool(self._speculator.user_reject_speculation(title))
                status = "rejected"
            return {
                "matched": applied,
                "domain": title,
                "status": status if applied else "",
            }
        if kind != "confusion":
            raise RuntimeError(f"Stored dialogue settlement kind is invalid: {kind!r}")
        try:
            confusion_id = int(payload.get("confusion_id", 0))
            generation = max(0, int(payload.get("anchor_generation", 0)))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Stored confusion settlement payload is invalid") from exc
        confusion = self._confusion_manager.get(confusion_id)
        if confusion is not None and confusion.status in {"resolved", "dismissed"}:
            return {
                "matched": True,
                "confusion_id": confusion_id,
                "status": confusion.status,
                "interpretation": str(payload.get("interpretation", "")),
            }
        if generation > 0:
            terminal = self._confusion_manager.process_anchor_settlement(
                confusion_id,
                action="resolve",
                interpretation=str(payload.get("interpretation", "")),
                note=str(payload.get("note", "")),
                turn_id=str(settlement.get("turn_id", "")),
                anchor_generation=generation,
            )
        else:
            terminal = self._confusion_manager.resolve(
                confusion_id,
                resolution=str(payload.get("interpretation", "")),
                note=str(payload.get("note", "")),
            )
        if terminal is None:
            return None
        return {
            "matched": True,
            "confusion_id": confusion_id,
            "status": terminal,
            "interpretation": str(payload.get("interpretation", "")),
        }

    async def _apply_dialogue_settlement_derived(
        self,
        *,
        settlement: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[str]:
        """Upsert revise-derived hypotheses and return their stable rebuild refs."""
        kind = str(payload.get("kind", "hypothesis")).strip().lower()
        verdict = str(settlement.get("verdict", "")).strip().lower()
        if kind != "hypothesis" or verdict != "revised":
            return []
        return await self._persist_anchor_derived_hypotheses(
            _as_dict_list(payload.get("derived")),
        )

    def _dialogue_settlement_rebuild_trigger_refs(
        self,
        *,
        settlement: dict[str, Any],
        payload: dict[str, Any],
        result: dict[str, Any],
        derived_trigger_refs: list[str],
    ) -> list[str]:
        """Build the complete marker set so one retry cannot refresh its clock."""
        kind = str(payload.get("kind", "hypothesis")).strip().lower()
        if kind != "hypothesis":
            return []
        refs: list[str] = []
        feedback = self._settlement_feedback(
            str(payload.get("title", "")),
            str(settlement.get("verdict", "")),
        )
        signal = str(feedback.get("signal", "")).strip().lower()
        if bool(result.get("matched", False)) and signal in {
            "confirm",
            "like",
            "support",
            "reject",
            "dislike",
            "deny",
        }:
            refs.append(f"insight_feedback:{signal}:{str(result.get('hypothesis', ''))[:60]}")
        for trigger_ref in derived_trigger_refs:
            if trigger_ref and trigger_ref not in refs:
                refs.append(trigger_ref)
        return refs

    def _record_dialogue_settlement_ledgers(
        self,
        *,
        ref: str,
        settlement: dict[str, Any],
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Best-effort audit effects keyed independently from business apply."""
        kind = str(payload.get("kind", "hypothesis")).strip().lower()
        title = str(payload.get("title", "")).strip()
        verdict = str(settlement.get("verdict", "")).strip().lower()
        source = str(payload.get("source", "")).strip()
        turn_id = str(settlement.get("turn_id", ""))
        write_point = {
            "hypothesis": "settle_insight",
            "confusion": "settle_confusion",
            "speculation": "settle_speculation",
        }.get(kind)
        if write_point is None:
            raise RuntimeError(f"Stored dialogue settlement kind is invalid: {kind!r}")
        source_refs = [ref]
        for key in ("source_turn_id", "source_reply_to_turn_id", "source_context_digest"):
            value = str(payload.get(key, "")).strip()
            if value:
                source_refs.append(f"{key}:{value}")
        self._ledger.record(
            write_point=write_point,
            source=source,
            before={"title": title, "verdict": verdict},
            after={"matched": bool(result.get("matched", False))},
            source_refs=source_refs,
            outcome="success" if bool(result.get("matched", False)) else "failed",
            turn_id=turn_id,
            effect_key=self._dialogue_settlement_ledger_effect_key(ref),
        )
        if kind != "hypothesis" or verdict != "revised":
            return
        current = {self._normalize_text(item.hypothesis): item for item in self._load_insights()}
        for item in _as_dict_list(payload.get("derived")):
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            normalized = self._normalize_text(content)
            hypothesis = current.get(normalized)
            confidence = (
                hypothesis.confidence
                if hypothesis is not None
                else round(
                    max(0.75, min(1.0, self._to_float(item.get("confidence", 0.0)))),
                    4,
                )
            )
            self._ledger.record(
                write_point="anchor_revise_derived",
                source="dialogue_anchor",
                after={"hypothesis": content, "confidence": confidence},
                source_refs=[content[:60]],
                turn_id=turn_id,
                effect_key=self._dialogue_settlement_derived_ledger_effect_key(
                    ref,
                    normalized,
                ),
            )

    @staticmethod
    def _dialogue_settlement_ledger_effect_key(ref: str) -> str:
        ref_digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()
        return f"dialogue:{ref_digest}:ledger"

    @staticmethod
    def _dialogue_settlement_derived_ledger_effect_key(
        ref: str,
        normalized_content: str,
    ) -> str:
        ref_digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()
        content_digest = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        return f"dialogue:{ref_digest}:derived:{content_digest}"

    def _dialogue_settlement_stored_result(
        self,
        settlement: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        stored_result = settlement.get("result")
        if isinstance(stored_result, dict) and stored_result:
            return dict(stored_result)
        return self._read_dialogue_settlement_result(settlement, payload)

    def _reconcile_applied_dialogue_settlement_publication(
        self,
        *,
        ref: str,
        settlement: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Replay only observer/projection/anchor effects after applied=1."""
        result = self._dialogue_settlement_stored_result(settlement, payload)
        self._record_dialogue_settlement_ledgers(
            ref=ref,
            settlement=settlement,
            payload=payload,
            result=result,
        )
        self._project_dialogue_settlement(ref, settlement)
        self._dialogue_settlement_checkpoint("after_projection", ref)
        self._publish_dialogue_settlement_anchor(ref, settlement)
        self._dialogue_settlement_checkpoint("after_anchor_release", ref)
        return result

    def _dialogue_settlement_checkpoint(self, checkpoint: str, ref: str) -> None:
        """No-op seam for exact crash-boundary fault injection."""
        del checkpoint, ref

    def _read_dialogue_settlement_result(
        self,
        settlement: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        kind = str(payload.get("kind", "hypothesis")).strip().lower()
        if kind == "hypothesis":
            return self.feedback_result(
                self._settlement_feedback(
                    str(payload.get("title", "")),
                    str(settlement.get("verdict", "")),
                )
            )
        if kind == "speculation":
            domain = str(payload.get("title", "")).strip()
            state = self._speculator._load_state()
            active = next(
                (item for item in state.active if item.domain.casefold() == domain.casefold()),
                None,
            )
            cooldown = next(
                (item for item in state.cooldown if item.domain.casefold() == domain.casefold()),
                None,
            )
            status = active.status if active is not None else ("rejected" if cooldown else "")
            return {
                "matched": bool(status),
                "domain": domain,
                "status": status,
            }
        try:
            confusion_id = int(payload.get("confusion_id", 0))
        except (TypeError, ValueError):
            confusion_id = 0
        confusion = self._confusion_manager.get(confusion_id) if confusion_id else None
        return {
            "matched": confusion is not None,
            "confusion_id": confusion_id,
            "status": confusion.status if confusion is not None else "",
            "interpretation": str(payload.get("interpretation", "")),
        }

    @staticmethod
    def _settlement_feedback(title: str, verdict: str) -> dict[str, Any]:
        signal = "confirm" if verdict.strip().lower() == "confirmed" else "reject"
        return {"hypothesis": title, "signal": signal}

    def _project_dialogue_settlement(
        self,
        ref: str,
        settlement: dict[str, Any],
    ) -> None:
        """Idempotently project an applied receipt to every matching card."""
        if int(settlement.get("applied", 0)) != 1:
            return
        payload = settlement.get("payload")
        stored_payload = dict(payload) if isinstance(payload, dict) else {}
        kind = str(stored_payload.get("kind", "hypothesis")).strip().lower()
        if kind == "hypothesis":
            database = self._ledger_database
            project = getattr(database, "project_applied_card_settlement", None)
            if callable(project):
                project(ref)

    def _publish_dialogue_settlement_anchor(
        self,
        ref: str,
        settlement: dict[str, Any],
    ) -> None:
        """Release only the exact frozen generation from an applied receipt."""
        if int(settlement.get("applied", 0)) != 1:
            return
        payload = settlement.get("payload")
        stored_payload = dict(payload) if isinstance(payload, dict) else {}
        kind = str(stored_payload.get("kind", "hypothesis")).strip().lower()
        anchor = self._dialogue_anchor_manager.current()
        if anchor is None or anchor.kind != kind or anchor.ref != ref:
            return
        try:
            expected_generation = max(0, int(stored_payload.get("anchor_generation", 0)))
        except (TypeError, ValueError):
            expected_generation = 0
        if expected_generation <= 0 or anchor.generation != expected_generation:
            logger.warning(
                "dialogue settlement anchor release fenced: ref=%r receipt_generation=%s "
                "active_generation=%s",
                ref,
                expected_generation,
                anchor.generation,
            )
            return
        if kind == "hypothesis":
            verdict = str(settlement.get("verdict", "")).strip().lower()
            card_state = {
                "confirmed": "confirmed",
                "revised": "revised",
            }.get(verdict, "rejected")
            self._dialogue_anchor_manager.release(
                reason="settled",
                card_state=card_state,
                expected_generation=expected_generation,
            )
        else:
            self._dialogue_anchor_manager.release(
                reason="settled",
                expected_generation=expected_generation,
            )

    def _dialogue_settlement_response(
        self,
        *,
        outcome: str,
        settlement: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = settlement.get("payload")
        stored_payload = dict(payload) if isinstance(payload, dict) else {}
        kind = str(stored_payload.get("kind", "hypothesis")).strip().lower()
        stored_verdict = str(settlement.get("verdict", "")).strip().lower()
        if kind == "confusion" or stored_verdict.startswith("answer:"):
            public_verdict = "answered"
        else:
            public_verdict = {
                "confirmed": "confirmed",
                "revised": "revised",
            }.get(stored_verdict, "rejected")
        response: dict[str, Any] = {
            "ok": outcome != "processing",
            "outcome": outcome,
            "verdict": public_verdict,
            "settlement_verdict": stored_verdict,
            "state": public_verdict if outcome != "processing" else "processing",
        }
        effective_result = result or self._read_dialogue_settlement_result(
            settlement,
            stored_payload,
        )
        response.update(effective_result)
        return response

    def _sync_insight_to_soul_snapshot(
        self,
        *,
        target_normalized: str,
        validated: bool,
        confidence: float,
    ) -> None:
        """Mirror an insight calibration onto the soul layer's active_insights.

        No-op when the soul profile has no matching active insight (e.g. the
        hypothesis exists only in the insight layer, not in the surfaced
        window). Re-syncs the human-readable profile files on change.
        """
        soul_layer = self._memory.get_layer("soul")
        if not soul_layer.data:
            return
        try:
            profile = OnionProfile.from_dict(soul_layer.data)
        except Exception:
            logger.debug("Failed to load OnionProfile for insight snapshot sync", exc_info=True)
            return
        changed = False
        for insight in profile.active_insights:
            if self._normalize_text(insight.hypothesis) == target_normalized:
                insight.validated = validated
                insight.confidence = confidence
                changed = True
        if not changed:
            return
        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()
        try:
            self._memory.sync_profile_files(profile)
        except Exception:
            logger.debug("sync_profile_files after insight feedback failed", exc_info=True)

    async def learn_from_dialogue(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        session: str,
        scope: str = "chat",
        turn_id: str = "",
        anchor_ref: str = "",
        anchor_generation: int = 0,
        dialogue_binding: Any | Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Persist a chat turn and update long-term understanding when warranted.

        ``scope`` / ``turn_id`` (Phase 1) are threaded from the durable chat
        path. ``scope`` defaults to ``"chat"``; only unanchored ``"chat"``
        turns run inventory ``settles``. Probe settlement stays in its durable
        side effect; confusion settlement belongs exclusively to the serialized
        dialogue-anchor processor. ``turn_id`` is stamped on ledger rows as an
        idempotency observation key. Provider calls retain their own finite
        timeouts; this mutation-bearing method intentionally has no whole-job
        timeout that could cancel it between local effects.
        """
        binding = None
        binding_context = None
        inventory_settles_allowed = True
        if dialogue_binding is not None:
            from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding

            if isinstance(dialogue_binding, DialogueTurnBinding):
                binding = dialogue_binding
            elif isinstance(dialogue_binding, Mapping):
                binding = DialogueTurnBinding.from_mapping(dialogue_binding)
            else:
                raise TypeError("dialogue_binding must be DialogueTurnBinding or a mapping")
            inventory_settles_allowed = binding.inventory_settles_allowed
            binding_context = binding.context
            if binding_context is not None:
                if anchor_ref and anchor_ref != binding_context.ref:
                    raise ValueError("learn anchor_ref conflicts with frozen dialogue context")
                if anchor_generation and anchor_generation != binding_context.generation:
                    raise ValueError(
                        "learn anchor_generation conflicts with frozen dialogue context"
                    )
                anchor_ref = binding_context.ref
                anchor_generation = binding_context.generation
            else:
                # New ordinary/detached API turns explicitly have no anchor.
                anchor_ref = ""
                anchor_generation = 0

        def _dialogue_event(*, status: str) -> dict[str, object]:
            metadata: dict[str, object] = {
                "user_message": user_message,
                "assistant_reply": assistant_reply,
                "source": "chat",
                "session": session,
            }
            title = user_message[:60]
            event: dict[str, object] = {
                "event_type": "dialogue",
                "title": title,
                "metadata": metadata,
            }
            if binding is not None:
                metadata.update(
                    {
                        "turn_id": turn_id,
                        "reply_to_turn_id": (
                            binding_context.reply_to_turn_id if binding_context is not None else ""
                        ),
                        "binding_mode": binding.mode.value,
                        "binding_status": status,
                        "context_digest": binding.context_digest,
                        "anchor_kind": (
                            binding_context.kind if binding_context is not None else ""
                        ),
                        "anchor_ref": binding_context.ref if binding_context is not None else "",
                        "anchor_generation": (
                            binding_context.generation if binding_context is not None else 0
                        ),
                        "context_title": (
                            binding_context.title if binding_context is not None else ""
                        ),
                    }
                )
            if binding_context is not None:
                label = "卡片" if binding_context.source_type == "card" else "疑惑问题"
                title = f"回复{label}「{binding_context.title}」：{user_message[:60]}"
                event["title"] = title
                event["context"] = (
                    f"用户在回复{label}「{binding_context.title}」时说：{user_message}\n"
                    f"阿B 回复：{assistant_reply}"
                )
            return event

        active_list, insight_hash_map = self._build_dialogue_active_list()
        active_anchor: DialogueAnchor | None = None
        anchor_context: dict[str, object] | None = None
        anchor_texts: list[str] = []
        if binding_context is not None:
            self._dialogue_anchor_manager.expire()
            active_anchor = self._dialogue_anchor_manager.validate_snapshot(
                anchor_ref,
                anchor_generation,
            )
            if active_anchor is None:
                await self._memory.propagate_event(_dialogue_event(status="stale"))
                return self._stale_anchor_drop_result(
                    anchor_ref=anchor_ref,
                    anchor_generation=anchor_generation,
                    turn_id=turn_id,
                    phase="pre_llm",
                    context_digest=binding.context_digest if binding is not None else "",
                )
            anchor_texts = [binding_context.title, *binding_context.evidence_labels]
            anchor_context = {
                "kind": binding_context.kind,
                "ref": binding_context.ref,
                "generation": binding_context.generation,
                "text": binding_context.title,
                "object_texts": anchor_texts,
                "ambiguous_count": active_anchor.ambiguous_count,
                "context_digest": binding.context_digest if binding is not None else "",
            }
        elif anchor_ref or anchor_generation:
            # Compatibility path for pre-binding callers (CLI/legacy direct).
            await self._memory.propagate_event(_dialogue_event(status="active"))
            self._dialogue_anchor_manager.expire()
            active_anchor = self._dialogue_anchor_manager.validate_snapshot(
                anchor_ref,
                anchor_generation,
            )
            if active_anchor is None:
                return self._stale_anchor_drop_result(
                    anchor_ref=anchor_ref,
                    anchor_generation=anchor_generation,
                    turn_id=turn_id,
                    phase="pre_llm",
                )
            anchor_context, anchor_texts = self._build_dialogue_anchor_context(active_anchor)
        else:
            await self._memory.propagate_event(_dialogue_event(status="not_applicable"))
        anchor_decision: dict[str, object] | None = None
        try:
            if anchor_context is None:
                # Preserve the pre-anchor invocation bytes/signature exactly.
                extract_result = await self._dialogue_insight_analyzer.extract(
                    user_message=user_message,
                    assistant_reply=assistant_reply,
                    core_memory=self._memory.get_core_memory(),
                    active_list=active_list,
                )
            else:
                extract_result = await self._dialogue_insight_analyzer.extract(
                    user_message=user_message,
                    assistant_reply=assistant_reply,
                    core_memory=self._memory.get_core_memory(),
                    active_list=active_list,
                    anchor=anchor_context,
                )
            # Tolerate the legacy list return as well as the new
            # {"candidates", "settles"} dict.
            if isinstance(extract_result, dict):
                extracted = _as_dict_list(extract_result.get("candidates"))
                settles = _as_dict_list(extract_result.get("settles"))
                raw_anchor_decision = extract_result.get("anchor")
                if isinstance(raw_anchor_decision, dict):
                    anchor_decision = raw_anchor_decision
            else:
                extracted = [dict(item) for item in extract_result if isinstance(item, dict)]
                settles = []
        except DialogueInsightAnalysisError:
            logger.exception("Failed to extract dialogue insight candidates.")
            extracted = []
            settles = []

        # The LLM call above yields control for an unbounded interval. An API
        # action, TTL release, or newer dialogue entry may replace the anchor
        # while it is in flight. Re-read and compare the ref+generation pair
        # immediately after the response and before *any* object, replay-queue,
        # candidate, or profile side effect. A stale answer is observation-only:
        # discard every parsed output and leave the new generation untouched.
        if active_anchor is not None:
            revalidated_anchor = self._dialogue_anchor_manager.validate_snapshot(
                anchor_ref,
                anchor_generation,
            )
            if revalidated_anchor is None:
                if binding_context is not None:
                    await self._memory.propagate_event(_dialogue_event(status="stale"))
                return self._stale_anchor_drop_result(
                    anchor_ref=anchor_ref,
                    anchor_generation=anchor_generation,
                    turn_id=turn_id,
                    phase="post_llm",
                    context_digest=binding.context_digest if binding is not None else "",
                )
            active_anchor = revalidated_anchor

        if binding_context is not None:
            assert binding is not None
            await self._memory.propagate_event(_dialogue_event(status="active"))
            for candidate in extracted:
                candidate.setdefault("source_turn_id", turn_id)
                candidate.setdefault("source_reply_to_turn_id", binding_context.reply_to_turn_id)
                candidate.setdefault("source_context_digest", binding.context_digest)

        anchor_outcome = ""
        if active_anchor is not None:
            extracted = self._filter_anchor_overlap_candidates(extracted, anchor_texts)
            if anchor_decision is None:
                logger.warning(
                    "dialogue anchor decision missing/invalid; keeping generation=%s",
                    active_anchor.generation,
                )
                anchor_outcome = "kept_invalid"
            else:
                anchor_outcome = await self._process_dialogue_anchor_decision(
                    anchor=active_anchor,
                    anchor_texts=anchor_texts,
                    decision=anchor_decision,
                    turn_id=turn_id,
                    binding_provenance=(
                        {
                            "source_turn_id": turn_id,
                            "source_reply_to_turn_id": binding_context.reply_to_turn_id,
                            "source_context_digest": binding.context_digest,
                            "source_binding_mode": binding.mode.value,
                        }
                        if binding_context is not None and binding is not None
                        else None
                    ),
                )
                if anchor_outcome == "stale":
                    return self._stale_anchor_drop_result(
                        anchor_ref=anchor_ref,
                        anchor_generation=anchor_generation,
                        turn_id=turn_id,
                        phase="generation_cas",
                        context_digest=binding.context_digest if binding is not None else "",
                    )

        # Process inventory settles (single ownership, spec §invariant 6): an
        # anchored turn belongs to the dialogue-anchor processor. Probe turns
        # retain their durable side effect, so both paths are excluded here.
        if scope == "chat" and settles and active_anchor is None and inventory_settles_allowed:
            await self._process_dialogue_settles(
                settles=settles,
                active_list=active_list,
                insight_hash_map=insight_hash_map,
                turn_id=turn_id,
                admission_anchor_ref=anchor_ref,
                admission_anchor_generation=anchor_generation,
            )

        merged_candidates = self._merge_insight_candidates(
            self._memory.load_insight_candidates(),
            extracted,
        )
        self._memory.save_insight_candidates(merged_candidates)
        self._record_immediate_dialogue_cognition(merged_candidates)
        eligible_candidates = [
            item for item in merged_candidates if self._candidate_ready_for_learning(item)
        ]
        if not eligible_candidates:
            return self._with_anchor_outcome(
                {
                    "event_logged": True,
                    "candidate_count": len(extracted),
                    "preference_updated": False,
                    "profile_rebuilt": False,
                },
                anchor_outcome,
            )

        # Posture-gate access point ① (Phase 3): interest/dislike take the fast
        # line unchanged; goal/value/state deep candidates pass the gate. In
        # ``off`` this is a no-op (byte-identical feed); in ``shadow`` every deep
        # candidate stays but its judgement is recorded asynchronously; only
        # ``enforce`` drops rejected candidates and demotes downgraded ones to
        # insight hypotheses (confidence × 0.6).
        gated_candidates = await self._gate_dialogue_candidates(eligible_candidates)
        # Dialogue fast lane (user decision 2026-07-27): a gate-accepted deep
        # candidate is the user's own first-person statement — that IS the
        # confirmation the deep unique mode asks for. Persist it as a validated
        # hypothesis so it becomes a durable rebuild input instead of vanishing
        # after one preference prompt, and force a same-turn rebuild below even
        # when no interest weight moved (a pure self-statement often doesn't).
        accepted_deep_candidates = [
            item
            for item in gated_candidates
            if str(item.get("kind", "")).strip() in _DEEP_CANDIDATE_KINDS
        ]
        if accepted_deep_candidates:
            self._persist_confirmed_deep_candidates(accepted_deep_candidates, turn_id=turn_id)
        if not gated_candidates:
            return self._with_anchor_outcome(
                {
                    "event_logged": True,
                    "candidate_count": len(extracted),
                    "preference_updated": False,
                    "profile_rebuilt": False,
                },
                anchor_outcome,
            )

        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        existing_profile = dict(self._memory.get_layer("soul").data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=[
                {
                    "event_type": "dialogue_insight",
                    "title": str(item.get("content", "")),
                    "metadata": {
                        "kind": item.get("kind", ""),
                        "confidence": item.get("confidence", 0.0),
                        "evidence": item.get("evidence", ""),
                        "source": "dialogue",
                        "occurrences": item.get("occurrences", 1),
                    },
                }
                for item in gated_candidates
            ],
            existing_preference=existing_preference,
        )
        old_disliked = {
            str(item).strip()
            for item in self._as_str_list(existing_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        new_disliked = {
            str(item).strip()
            for item in self._as_str_list(updated_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        newly_added_dislikes = sorted(new_disliked - old_disliked)
        candidate_refs = self._candidate_ledger_refs(eligible_candidates)
        if binding_context is not None and binding is not None:
            candidate_refs.extend(
                [
                    f"source_turn_id:{turn_id}",
                    f"source_reply_to_turn_id:{binding_context.reply_to_turn_id}",
                    f"source_context_digest:{binding.context_digest}",
                ]
            )
        # Topic freeze (Phase 2): a topic under an unresolved confusion must not
        # be further reinforced. New/upgraded weights for frozen topics are held
        # back here (existing weights untouched); no-op when nothing is frozen,
        # so a confusion-free database yields a byte-identical write.
        try:
            frozen_topics = self._confusion_manager.frozen_topics()
        except Exception:
            frozen_topics = set()
        if frozen_topics:
            updated_preference, held_updates = apply_confusion_freeze(
                before=existing_preference,
                after=updated_preference,
                frozen_topics=frozen_topics,
            )
            if held_updates:
                try:
                    self._confusion_manager.record_held_updates(held_updates)
                except Exception:
                    logger.debug("Failed to record held confusion updates", exc_info=True)
        # Topic-lifecycle (Phase 4): count this dialogue as evidence, then
        # archive any newly disliked topic (归档+避雷). Archive wins over the
        # evidence promotion for the same topic.
        self._apply_topic_lifecycle_evidence(existing_preference, updated_preference)
        if newly_added_dislikes:
            self._archive_disliked_topics(updated_preference, newly_added_dislikes)
        # Ledger write point D5 #1a: dialogue-driven preference overwrite.
        with self._ledger.action(
            write_point="dialogue_preference_overwrite",
            source="chat",
            before=existing_preference,
            source_refs=candidate_refs,
            turn_id=turn_id,
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)

        if newly_added_dislikes:
            # Start the deterministic purge as soon as the durable preference
            # write succeeds. A full profile rebuild can take tens of seconds;
            # it must not delay removing an explicitly rejected topic from the
            # active pool. Semantic recall continues detached in parallel.
            # Ledger write point D5 #2: dislike purge (records the intent at
            # schedule time; the detached recall itself is best-effort).
            self._ledger.record(
                write_point="dislike_purge",
                source="chat",
                before={"disliked_topics": sorted(old_disliked)},
                after={"disliked_topics": sorted(new_disliked)},
                source_refs=list(newly_added_dislikes),
                outcome="success",
                turn_id=turn_id,
            )
            self._schedule_dislike_purge(
                newly_added=newly_added_dislikes,
                all_dislikes=sorted(new_disliked),
                database=getattr(self._memory, "_database", None),
                embedding_service=self._embedding_service,
                llm_service=self._llm_service,
            )

        profile_rebuilt = False
        rebuild_gate_ok = (
            self._preference_changed_significantly(existing_preference, updated_preference)
            or bool(accepted_deep_candidates)
        ) and not (
            await self._gate_soul_rebuild(
                trigger=_REBUILD_TRIGGER_DIALOGUE,
                existing_preference=existing_preference,
                updated_preference=updated_preference,
                source_refs=candidate_refs,
                context={"candidate_refs": candidate_refs},
            )
        ).blocks
        if rebuild_gate_ok:
            # Ledger write point D5 #1b: dialogue-driven full soul rebuild.
            try:
                with self._ledger.action(
                    write_point="dialogue_soul_rebuild",
                    source="chat",
                    before=existing_profile,
                    source_refs=candidate_refs,
                    turn_id=turn_id,
                ) as _entry:
                    legacy_profile = await self._profile_builder.build(
                        history=[],
                        preference=updated_preference,
                        awareness_notes=[
                            awareness_note_to_dict(item) for item in self._load_awareness_notes()
                        ],
                        active_insights=self._rebuild_active_insights(),
                    )
                    profile = OnionProfile.from_legacy(legacy_profile)
                    profile.populate_from_flat_preference(updated_preference)
                    soul_layer = self._memory.get_layer("soul")
                    soul_layer.data.clear()
                    soul_layer.data.update(profile.to_dict())
                    soul_layer.save()
                    _entry.after = dict(soul_layer.data)
                self._memory.sync_profile_files(profile)
                profile_rebuilt = True
            except Exception:
                logger.exception("Failed to rebuild soul profile after dialogue learning.")

        self._record_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=existing_profile,
            current_profile=dict(self._memory.get_layer("soul").data),
            source="chat",
        )

        for item in merged_candidates:
            if self._candidate_ready_for_learning(item):
                item["applied"] = True
                item["updated_at"] = datetime.now().isoformat()
        self._memory.save_insight_candidates(merged_candidates)

        # Next dialogue-learning pass also triggers the debounced confirmed-
        # hypotheses rebuild (spec invariant 4). Best-effort.
        try:
            await self.run_pending_rebuild_if_due()
        except Exception:
            logger.debug("pending rebuild trigger (dialogue) failed", exc_info=True)

        return self._with_anchor_outcome(
            {
                "event_logged": True,
                "candidate_count": len(extracted),
                "preference_updated": True,
                "profile_rebuilt": profile_rebuilt,
            },
            anchor_outcome,
        )

    async def prepare_profile_event_owner_cutover(self) -> dict[str, object]:
        """Fence legacy direct-ingested rows before the first event-only write."""
        if not self.is_profile_ready():
            return {
                "prepared": False,
                "profile_event_owner_version": 0,
                "fenced_event_id": 0,
                "reason": "profile_not_ready",
            }
        checkpoint = self._pipeline.consumer_checkpoint(_PROFILE_EVENT_CONSUMER)
        owner_version = self._to_int(checkpoint.get("owner_version", 0))
        if owner_version >= 1:
            return {
                "prepared": False,
                "profile_event_owner_version": owner_version,
                "fenced_event_id": self._to_int(checkpoint.get("cursor", 0)),
            }
        async with self._profile_event_lock:
            return await self._prepare_profile_event_owner_cutover_locked()

    async def _prepare_profile_event_owner_cutover_locked(self) -> dict[str, object]:
        checkpoint = self._pipeline.consumer_checkpoint(_PROFILE_EVENT_CONSUMER)
        owner_version = self._to_int(checkpoint.get("owner_version", 0))
        cursor = self._to_int(checkpoint.get("cursor", 0))
        if owner_version >= 1:
            return {
                "prepared": False,
                "profile_event_owner_version": owner_version,
                "fenced_event_id": cursor,
            }
        fence = max(cursor, self._to_int(self._memory.get_latest_event_id()))
        cutover_at = datetime.now().isoformat()
        await self._pipeline.checkpointed_enqueue_batch(
            [],
            consumer=_PROFILE_EVENT_CONSUMER,
            cursor=fence,
            owner_version=1,
            cutover_at=cutover_at,
            cutover_event_id=fence,
        )
        logger.info("Generic profile event owner cut over at event id %d", fence)
        return {
            "prepared": True,
            "profile_event_owner_version": 1,
            "fenced_event_id": fence,
            "cutover_at": cutover_at,
        }

    async def process_profile_events_if_needed(self) -> dict[str, object]:
        """Continuously claim explicitly generic-owned event rows."""
        if not self.is_profile_ready():
            return {
                "triggered": False,
                "scanned": 0,
                "enqueued": 0,
                "reason": "profile_not_ready",
            }
        if self._profile_event_lock.locked():
            return {
                "triggered": False,
                "scanned": 0,
                "enqueued": 0,
                "reason": "profile_event_batch_in_progress",
            }
        scanned_count = 0
        enqueued_count = 0
        async with self._profile_event_lock:
            await self._prepare_profile_event_owner_cutover_locked()
            while True:
                checkpoint = self._pipeline.consumer_checkpoint(_PROFILE_EVENT_CONSUMER)
                cursor = self._to_int(checkpoint.get("cursor", 0))
                rows = self._memory.query_event_rows_after(
                    after_event_id=cursor,
                    limit=_PROFILE_EVENT_BATCH_LIMIT,
                )
                if not rows:
                    break
                events = [self._deserialize_event(row) for row in rows]
                last_scanned_id = max(
                    (self._to_int(event.get("id", 0)) for event in events),
                    default=cursor,
                )
                owned = [event for event in events if self._generic_owner_owns_event(event)]
                project_retractions = getattr(
                    self._memory,
                    "apply_retraction_db_marks",
                    None,
                )
                if callable(project_retractions) and owned:
                    # Retraction projection is part of this durable owner's
                    # claim protocol. It must finish before the cursor moves;
                    # a failure therefore retries after restart and never
                    # extends HTTP request latency.
                    await asyncio.to_thread(project_retractions, owned)
                signals = [self._profile_event_to_signal(event) for event in owned]
                result = await self._pipeline.checkpointed_enqueue_batch(
                    signals,
                    consumer=_PROFILE_EVENT_CONSUMER,
                    cursor=last_scanned_id,
                    owner_version=1,
                )
                scanned_count += len(events)
                enqueued_count += int(getattr(result, "signals_accepted", 0))
                if len(rows) < _PROFILE_EVENT_BATCH_LIMIT:
                    break
            # Recovery for checkpoint->consume crashes also reaches this path when no
            # new rows exist. A checkpoint failure above propagates and skips it.
            flush = await self._pipeline.tick_if_buffered()
        return {
            "triggered": bool(getattr(flush, "layers_updated", [])),
            "scanned": scanned_count,
            "enqueued": enqueued_count,
            "layers_updated": [
                getattr(getattr(item, "layer", None), "value", "")
                for item in getattr(flush, "layers_updated", [])
            ],
        }

    @staticmethod
    def _generic_owner_owns_event(event: dict[str, Any]) -> bool:
        metadata = event.get("metadata")
        return bool(
            isinstance(metadata, dict)
            and str(metadata.get("profile_update_owner") or "").strip().lower() == "generic"
        )

    @staticmethod
    def _profile_event_to_signal(event: dict[str, Any]) -> ProfileSignal:
        event_id = SoulEngine._to_int(event.get("id", 0))
        if event_id <= 0:
            raise ValueError("durable profile event row requires a positive id")
        if not SoulEngine._generic_owner_owns_event(event):
            raise ValueError("durable profile event is not owned by the generic consumer")
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        signal_id = f"event-row-{event_id}"
        if (
            str(metadata.get("event_namespace") or "").strip().lower() == "recommendation"
            and str(metadata.get("source") or "").strip().lower() == "recommendation_click"
        ):
            recommendation_id = SoulEngine._to_int(metadata.get("recommendation_id", 0))
            return signal_from_recommendation_click(
                bvid=str(metadata.get("bvid") or metadata.get("content_id") or ""),
                title=str(event.get("title") or ""),
                recommendation_id=recommendation_id or None,
                topic_label=str(metadata.get("topic_label") or ""),
                up_name=str(metadata.get("up_name") or ""),
                content_id=str(metadata.get("content_id") or ""),
                content_url=str(metadata.get("content_url") or event.get("url") or ""),
                source_platform=str(metadata.get("source_platform") or ""),
                signal_id=signal_id,
            )
        return signal_from_event(event, signal_id=signal_id)

    async def process_feedback_batch_if_needed(self) -> dict[str, object]:
        """Reanalyze preference/profile after enough new feedback has accumulated.

        Wave B shim (spec 2026-07-27 Phase 3). The method name is the coupling
        point for all three external callers (``runtime/feedback_scheduler.py``,
        the CLI feedback command, ``integrations/openclaw/operations.py``), so
        they stay zero-change across the merge:

        - ``unified_interest_line`` OFF → the legacy feedback batch verbatim.
        - ON → continuously claim durable feedback rows after the cursor,
          atomically checkpoint stable-ID signals with that cursor, then consume
          buffered recovery work outside the HTTP request.

        None of the three callers reads the returned dict (each awaits and
        discards it), but the legacy keys are preserved anyway so the shape
        stays a stable contract.
        """
        if self._unified_interest_line:
            return await self._process_feedback_via_unified_line()
        return await self._process_feedback_batch_legacy()

    async def prepare_feedback_owner_cutover(self) -> dict[str, object]:
        """Fence the v1 direct-ingest owner before accepting v2 event-only writes.

        v0.3.191 wrote live feedback to both the event ledger and the pipeline,
        while its one-shot feedback cursor stayed at the migration watermark.
        On the first v2 run, rows above that old cursor therefore cannot be
        claimed safely: replaying them would learn the same user action twice.

        If the v1 migration marker exists, advance the cursor to the latest
        feedback row visible *at this call boundary* and publish owner version
        2 in the same state write. API startup and direct adapters call this
        before writing a new event, so every later row belongs to the durable
        cursor owner. A fresh install has no v1 marker; it records owner v2
        without moving the cursor, preserving any unclaimed durable rows.
        """
        if not self._unified_interest_line:
            return {
                "prepared": False,
                "feedback_owner_version": 0,
                "fenced_feedback_event_id": 0,
            }
        checkpoint = self._pipeline.consumer_checkpoint(_CONTENT_FEEDBACK_CONSUMER)
        owner_version = self._to_int(checkpoint.get("owner_version", 0))
        if owner_version >= 2 and bool(checkpoint.get("has_cutover_event_id", False)):
            return {
                "prepared": False,
                "feedback_owner_version": owner_version,
                "fenced_feedback_event_id": self._to_int(checkpoint.get("cursor", 0)),
                "legacy_cutover_event_id": self._to_int(checkpoint.get("cutover_event_id", 0)),
            }
        async with self._feedback_batch_lock:
            return await self._prepare_feedback_owner_cutover_locked()

    async def _prepare_feedback_owner_cutover_locked(self) -> dict[str, object]:
        """Publish the owner-v2 fence while ``_feedback_batch_lock`` is held."""
        state = self._memory.load_feedback_state()
        checkpoint = self._pipeline.consumer_checkpoint(_CONTENT_FEEDBACK_CONSUMER)
        owner_version = self._to_int(checkpoint.get("owner_version", 0))
        pipeline_cursor = self._to_int(checkpoint.get("cursor", 0))
        cutover_event_id = self._to_int(checkpoint.get("cutover_event_id", 0))
        has_cutover_event_id = bool(checkpoint.get("has_cutover_event_id", False))
        if owner_version >= 2:
            if not has_cutover_event_id:
                # Compatibility for an early owner-v2 snapshot written before
                # the explicit legacy boundary field existed. The cursor is
                # the only safe historical boundary; rows after it must carry
                # an explicit owner marker.
                await self._pipeline.checkpointed_enqueue_batch(
                    [],
                    consumer=_CONTENT_FEEDBACK_CONSUMER,
                    cursor=pipeline_cursor,
                    owner_version=2,
                    cutover_event_id=pipeline_cursor,
                )
                cutover_event_id = pipeline_cursor
            return {
                "prepared": False,
                "feedback_owner_version": owner_version,
                "fenced_feedback_event_id": pipeline_cursor,
                "legacy_cutover_event_id": cutover_event_id,
            }

        # The legacy feedback file is read exactly once as migration input.
        # Once owner v2 is published, pipeline_state.json is authoritative and
        # this file becomes a compatibility mirror/provenance record only.
        cursor = max(
            pipeline_cursor,
            self._to_int(state.get("last_processed_feedback_event_id", 0)),
        )
        legacy_marker = str(state.get("unified_interest_line_migrated_at", "")).strip()
        fence = cursor
        if legacy_marker:
            # Use the insertion-order cursor API, not query_events(limit=1):
            # browser backfill may carry an old created_at, while ownership is
            # defined by the durable SQLite row id.
            rows_after_cursor = self._memory.query_events_since(
                after_event_id=cursor,
                event_types=["feedback"],
            )
            fence = max(
                cursor,
                max(
                    (
                        self._to_int(self._deserialize_event(row).get("id", 0))
                        for row in rows_after_cursor
                    ),
                    default=0,
                ),
            )

        # Rows at or below this append-only watermark may use the legacy
        # predicate. Every later content-feedback row must name its owner.
        legacy_cutover_event_id = max(
            pipeline_cursor,
            self._to_int(self._memory.get_latest_event_id()),
        )
        cutover_at = datetime.now().isoformat()
        await self._pipeline.checkpointed_enqueue_batch(
            [],
            consumer=_CONTENT_FEEDBACK_CONSUMER,
            cursor=fence,
            owner_version=2,
            cutover_at=cutover_at,
            cutover_event_id=legacy_cutover_event_id,
        )
        self._memory.save_feedback_state(
            {
                "last_processed_feedback_event_id": fence,
                "last_feedback_reanalyzed_at": str(state.get("last_feedback_reanalyzed_at", "")),
                "unified_interest_line_migrated_at": legacy_marker,
                "feedback_owner_version": 2,
                "feedback_owner_cutover_at": cutover_at,
                "feedback_owner_cutover_event_id": legacy_cutover_event_id,
            }
        )
        logger.info(
            "Unified interest line feedback owner cut over to v2 at event id %d "
            "(legacy_direct_owner=%s)",
            fence,
            bool(legacy_marker),
        )
        return {
            "prepared": True,
            "feedback_owner_version": 2,
            "fenced_feedback_event_id": fence,
            "legacy_cutover_event_id": legacy_cutover_event_id,
            "legacy_direct_owner": bool(legacy_marker),
        }

    async def _process_feedback_batch_legacy(self) -> dict[str, object]:
        """The pre-unified-line feedback batch, unchanged.

        Reached whenever ``scheduler.unified_interest_line`` is false — the
        rollback path. Byte-identical to what ``process_feedback_batch_if_needed``
        did before the shim, so ``TestFeedbackBatchContract`` still pins it.
        """
        if self._feedback_batch_lock.locked():
            return {
                "triggered": False,
                "feedback_count": 0,
                "preference_updated": False,
                "profile_rebuilt": False,
                "skipped": True,
                "reason": "feedback_batch_in_progress",
            }
        async with self._feedback_batch_lock:
            result = await self._process_feedback_batch_if_needed_locked()
        # Consume any held updates left ``replaying`` by a resolved real-interest
        # confusion (Wave B held-replay leftover). Best-effort — a replay failure
        # never breaks feedback processing; the items stay ``replaying`` for a
        # later run (and startup crash recovery bounds the worst case).
        try:
            await self.replay_held_updates()
        except Exception:
            logger.debug("held-replay consumer failed", exc_info=True)
        # A periodic hook for the debounced confirmed-hypotheses rebuild (spec
        # invariant 4). Best-effort — never breaks feedback processing.
        try:
            await self.run_pending_rebuild_if_due()
        except Exception:
            logger.debug("pending rebuild trigger (feedback batch) failed", exc_info=True)
        return result

    async def _process_feedback_via_unified_line(self) -> dict[str, object]:
        """The unified interest line's replacement for the feedback batch.

        Claim every feedback event after the cursor, then publish the stable-ID
        signals and cursor together in one atomic ``pipeline_state.json``
        checkpoint.  ``tick_if_buffered()`` consumes recovery work without
        running unrelated periodic maintenance on an empty owner pass.
        ``/api/feedback`` and ``/api/events`` only persist rows and wake this
        owner; neither endpoint waits for an LLM or contends on the pipeline
        lock.
        """
        if self._feedback_batch_lock.locked():
            return {
                "triggered": False,
                "feedback_count": 0,
                "preference_updated": False,
                "profile_rebuilt": False,
                "skipped": True,
                "reason": "feedback_batch_in_progress",
            }
        rebuilds_before = self._pipeline_feedback_rebuilds
        updates: list[Any] = []
        async with self._feedback_batch_lock:
            # Safety net for scheduler/third-party callers. Production HTTP,
            # CLI, and OpenClaw prepare before writing their next event so the
            # first v2 row is never mistaken for v1 direct-owned history.
            await self._prepare_feedback_owner_cutover_locked()
            claimed, enqueue_updates = await self._migrate_legacy_feedback_cursor_if_needed()
            updates.extend(enqueue_updates)
            # Measured after durable enqueue but before tick: this is the exact
            # FEEDBACK evidence this pass puts in play, including a buffer
            # recovered from a prior checkpoint->consume crash.
            buffered = self._buffered_feedback_signal_count()
            flush = await self._pipeline.tick_if_buffered()
            updates.extend(getattr(flush, "layers_updated", []))
        interest_updates = [
            update
            for update in updates
            if getattr(getattr(update, "layer", None), "value", "") == OnionLayer.INTEREST.value
        ]
        # A periodic hook for the debounced confirmed-hypotheses rebuild (spec
        # invariant 4). Best-effort — never breaks feedback processing. The
        # held-replay consumer is NOT re-run here: on the unified line it is a
        # feedback-batch privilege that already ran inside
        # ``_after_pipeline_feedback_interest`` if (and only if) the drained
        # batch actually carried FEEDBACK signals.
        try:
            await self.run_pending_rebuild_if_due()
        except Exception:
            logger.debug("pending rebuild trigger (unified line) failed", exc_info=True)
        return {
            "triggered": bool(interest_updates),
            "feedback_count": buffered,
            # Legacy meaning: "the preference layer was rewritten", which the
            # batch reported unconditionally once it fired. A consumed INTEREST
            # batch is exactly that. Whether the rewrite produced visible changes
            # is the separate ``preference_changed`` key.
            "preference_updated": bool(interest_updates),
            "preference_changed": any(getattr(u, "changed", False) for u in interest_updates),
            "profile_rebuilt": self._pipeline_feedback_rebuilds > rebuilds_before,
            "unified_interest_line": True,
            # Compatibility key retained for callers/tests from the one-shot
            # migration rollout; it now means rows claimed in this pass.
            "migrated_feedback_events": claimed,
            "enqueued_feedback_events": claimed,
        }

    def _buffered_feedback_signal_count(self) -> int:
        """How many FEEDBACK signals the INTEREST buffer is holding right now.

        The unified line's analogue of the legacy batch's ``feedback_count``:
        the number the priority-flush threshold is compared against.
        """
        try:
            buffers = self._pipeline._buffers  # noqa: SLF001 - same-package read
            buf = buffers.get(OnionLayer.INTEREST.value)
            if buf is None:
                return 0
            return sum(
                1 for sig in buf.signals if sig.get("signal_type") == SignalType.FEEDBACK.value
            )
        except Exception:
            return 0

    async def _migrate_legacy_feedback_cursor_if_needed(self) -> tuple[int, list[Any]]:
        """Claim durable feedback rows into the pipeline, continuously.

        Returns ``(claimed_count, enqueue_updates)``. Enqueue-only currently
        produces no layer updates; the second item preserves the rollout
        helper's return shape while consumption happens exclusively in the
        following ``pipeline.tick()``.

        Signals are built with ``signal_from_feedback``, NOT ``signals_from_events``:
        the latter never emits ``SignalType.FEEDBACK`` (it only ever produces
        BEHAVIOR_EVENT / ENGAGEMENT_EVENT), so migrated rows would silently lose
        every feedback privilege — the priority flush, dislike archiving, the
        gated soul rebuild, and the ``source="feedback"`` ledger provenance.

        **Retractions are skipped.** The legacy batch excluded them from both the
        threshold count and the analysis input (they are neutralizations, not
        preference-learning input) and they have already had their effect on the
        rows they retracted at the time they were recorded. Ingesting historical
        retractions now would replay a discount against a preference layer that
        was computed without the corresponding positives — the retraction
        semantics change (exclude → discount) is a *forward* change for live
        signals only, and the A/B gate 3 covers it there. The cursor still
        advances past them so they are never rescanned.

        **Atomic checkpoint:** each signal ID is derived from the event row ID,
        and ``checkpointed_enqueue_batch`` publishes signals plus cursor in one
        locked atomic snapshot. A failed replace restores both in-memory
        values; a crash after the checkpoint but before consumption recovers
        the buffered signal on restart.

        ``unified_interest_line_migrated_at`` remains as rollout provenance but
        is no longer a gate: live rows after the original migration must keep
        flowing through this same cursor owner.
        """
        state = self._memory.load_feedback_state()
        checkpoint = self._pipeline.consumer_checkpoint(_CONTENT_FEEDBACK_CONSUMER)
        last_processed_id = self._to_int(checkpoint.get("cursor", 0))
        legacy_cutover_event_id = (
            self._to_int(checkpoint.get("cutover_event_id", 0))
            if bool(checkpoint.get("has_cutover_event_id", False))
            else None
        )
        scanned = [
            self._deserialize_event(event)
            for event in self._memory.query_events_since(
                after_event_id=last_processed_id,
                event_types=["feedback"],
            )
        ]
        # ``event_type=feedback`` is shared by direct content reactions,
        # hypothesis settlement, retractions, and imported snapshots. Only the
        # first category belongs to this owner; every scanned row still advances
        # the cursor so unrelated rows are not revisited forever.
        pending = [
            event
            for event in scanned
            if is_content_feedback_event(
                event,
                legacy_cutover_event_id=legacy_cutover_event_id,
            )
        ]
        last_scanned_id = max(
            (self._to_int(event.get("id", 0)) for event in scanned),
            default=0,
        )
        signals = [
            self._feedback_event_to_signal(
                event,
                legacy_cutover_event_id=legacy_cutover_event_id,
            )
            for event in pending
        ]
        enqueue_result = await self._pipeline.checkpointed_enqueue_batch(
            signals,
            consumer=_CONTENT_FEEDBACK_CONSUMER,
            cursor=max(last_scanned_id, last_processed_id),
            owner_version=2,
        )

        migrated_at = str(state.get("unified_interest_line_migrated_at", "")).strip()
        self._memory.save_feedback_state(
            {
                "last_processed_feedback_event_id": max(last_scanned_id, last_processed_id),
                # Not a re-analysis — leave the legacy timestamp alone.
                "last_feedback_reanalyzed_at": str(state.get("last_feedback_reanalyzed_at", "")),
                "unified_interest_line_migrated_at": migrated_at or datetime.now().isoformat(),
                # Compatibility mirror only.  pipeline_state.json remains the
                # authority, but narrow storage adapters must not erase these
                # provenance fields merely because they do not implement
                # MemoryManager's read-before-write preservation.
                "feedback_owner_version": self._to_int(checkpoint.get("owner_version", 0)),
                "feedback_owner_cutover_at": str(checkpoint.get("cutover_at", "")),
            }
        )
        if signals:
            logger.info(
                "Unified interest line: durably enqueued %d feedback event(s) past cursor %d",
                len(signals),
                last_processed_id,
            )
        return len(signals), list(getattr(enqueue_result, "layers_updated", []))

    @staticmethod
    def _feedback_event_to_signal(
        event: dict[str, Any],
        *,
        legacy_cutover_event_id: int | None = None,
    ) -> ProfileSignal:
        """Rebuild the FEEDBACK signal ``/api/feedback`` would have emitted."""
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        event_id = SoulEngine._to_int(event.get("id", 0))
        if event_id <= 0:
            raise ValueError("durable feedback event row requires a positive id")
        if not is_content_feedback_event(
            event,
            legacy_cutover_event_id=legacy_cutover_event_id,
        ):
            raise ValueError("durable feedback event is not a cursor-owned content reaction")
        return signal_from_feedback(
            str(metadata.get("feedback_type") or "").strip(),
            str(event.get("title") or ""),
            str(metadata.get("feedback_note") or ""),
            signal_id=f"feedback-event-{event_id}",
        )

    async def _process_feedback_batch_if_needed_locked(self) -> dict[str, object]:
        """Feedback batch implementation guarded by ``_feedback_batch_lock``."""
        state = self._memory.load_feedback_state()
        last_processed_id = self._to_int(state.get("last_processed_feedback_event_id", 0))
        all_feedback_events = [
            self._deserialize_event(event)
            for event in self._memory.query_events_since(
                after_event_id=last_processed_id,
                event_types=["feedback"],
            )
        ]
        # Retractions (X unlike/unbookmark) are neutralizations, not
        # preference-learning input — exclude them from BOTH the threshold
        # count and the LLM analysis batch. They still advance the cursor
        # below so they aren't rescanned every cycle.
        feedback_events = [
            event for event in all_feedback_events if not self._is_retraction_feedback(event)
        ]
        feedback_count = len(feedback_events)
        if feedback_count < self._feedback_batch_threshold:
            return {
                "triggered": False,
                "feedback_count": feedback_count,
                "preference_updated": False,
                "profile_rebuilt": False,
            }

        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        existing_profile = dict(self._memory.get_layer("soul").data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=[self._compact_feedback_event_for_analysis(event) for event in feedback_events],
            existing_preference=existing_preference,
            event_chunk_size=DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
        )
        old_disliked = {
            str(item).strip()
            for item in self._as_str_list(existing_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        new_disliked = {
            str(item).strip()
            for item in self._as_str_list(updated_preference.get("disliked_topics", []))
            if str(item).strip()
        }
        newly_added_dislikes = sorted(new_disliked - old_disliked)
        # Topic-lifecycle (Phase 4): evidence overlay + dislike archive.
        self._apply_topic_lifecycle_evidence(existing_preference, updated_preference)
        if newly_added_dislikes:
            self._archive_disliked_topics(updated_preference, newly_added_dislikes)
        feedback_refs = [
            f"feedback_event:{self._to_int(event.get('id', 0))}" for event in feedback_events
        ] or ["feedback_batch"]
        # Ledger write point D5 #4a: feedback-batch preference overwrite.
        with self._ledger.action(
            write_point="feedback_preference_overwrite",
            source="feedback",
            before=existing_preference,
            source_refs=feedback_refs,
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)

        profile_rebuilt = await self._gated_feedback_soul_rebuild(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            existing_profile=existing_profile,
            source_refs=feedback_refs,
            feedback_count=feedback_count,
        )

        if newly_added_dislikes:
            self._schedule_dislike_purge(
                newly_added=newly_added_dislikes,
                all_dislikes=sorted(new_disliked),
                database=getattr(self._memory, "_database", None),
                embedding_service=self._embedding_service,
                llm_service=self._llm_service,
            )

        self._record_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=existing_profile,
            current_profile=dict(self._memory.get_layer("soul").data),
            source="feedback",
        )

        # Advance past everything scanned (retractions included) so excluded
        # rows aren't rescanned each cycle.
        last_scanned_id = max(
            (self._to_int(event.get("id", 0)) for event in all_feedback_events),
            default=0,
        )
        self._memory.save_feedback_state(
            {
                "last_processed_feedback_event_id": last_scanned_id,
                "last_feedback_reanalyzed_at": datetime.now().isoformat(),
            }
        )
        return {
            "triggered": True,
            "feedback_count": feedback_count,
            "preference_updated": True,
            "profile_rebuilt": profile_rebuilt,
        }

    async def _gated_feedback_soul_rebuild(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        existing_profile: dict[str, Any],
        source_refs: list[str],
        feedback_count: int,
    ) -> bool:
        """Rebuild the whole soul after feedback, if the gate lets it through.

        P2 (spec r3/F4): a feedback-driven significant preference shift passes
        access point ③ — previously this rebuild bypassed every gate. off/shadow
        proceed; enforce downgrade/reject abandons the rebuild. Shared by the
        legacy feedback batch and the unified interest line so both triggers
        keep the exact same deep-write discipline. Returns whether the soul
        profile was actually rebuilt.
        """
        rebuild_ok = (
            self._preference_changed_significantly(existing_preference, updated_preference)
            and not (
                await self._gate_soul_rebuild(
                    trigger=_REBUILD_TRIGGER_FEEDBACK_BATCH,
                    existing_preference=existing_preference,
                    updated_preference=updated_preference,
                    source_refs=source_refs,
                    context={"feedback_count": feedback_count, "feedback_refs": source_refs},
                )
            ).blocks
        )
        if not rebuild_ok:
            return False
        # Ledger write point D5 #4b: feedback-batch full soul rebuild.
        try:
            with self._ledger.action(
                write_point="feedback_soul_rebuild",
                source="feedback",
                before=existing_profile,
                source_refs=source_refs,
            ) as _entry:
                legacy_profile = await self._profile_builder.build(
                    history=[],
                    preference=updated_preference,
                    awareness_notes=[
                        awareness_note_to_dict(item) for item in self._load_awareness_notes()
                    ],
                    active_insights=self._rebuild_active_insights(),
                )
                profile = OnionProfile.from_legacy(legacy_profile)
                profile.populate_from_flat_preference(updated_preference)
                soul_layer = self._memory.get_layer("soul")
                soul_layer.data.clear()
                soul_layer.data.update(profile.to_dict())
                soul_layer.save()
                _entry.after = dict(soul_layer.data)
            self._memory.sync_profile_files(profile)
            return True
        except Exception:
            logger.exception("Failed to rebuild soul profile after feedback refresh.")
            return False

    async def _after_pipeline_feedback_interest(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        source_refs: list[str],
        feedback_count: int,
    ) -> None:
        """Unified interest line: the batch's post-write privileges.

        Called by the pipeline right after a FEEDBACK-carrying INTEREST batch is
        persisted. Mirrors what ``_process_feedback_batch_if_needed_locked``
        does after its own preference write: the gated soul rebuild, then the
        held-replay consumer. Both are best-effort — neither may break the
        interest update.
        """
        existing_profile = dict(self._memory.get_layer("soul").data)
        try:
            if await self._gated_feedback_soul_rebuild(
                existing_preference=existing_preference,
                updated_preference=updated_preference,
                existing_profile=existing_profile,
                source_refs=source_refs,
                feedback_count=feedback_count,
            ):
                self._pipeline_feedback_rebuilds += 1
        except Exception:
            logger.exception("Gated soul rebuild after a pipeline feedback batch failed")
        # Consume any held updates left ``replaying`` by a resolved real-interest
        # confusion — same best-effort contract the legacy batch had.
        try:
            await self.replay_held_updates()
        except Exception:
            logger.debug("held-replay consumer failed", exc_info=True)

    def _compact_feedback_event_for_analysis(
        self,
        event: dict[str, object],
    ) -> dict[str, object]:
        """Keep only preference-relevant feedback fields before LLM analysis."""
        compact: dict[str, object] = {}
        for key in (
            "id",
            "event_type",
            "url",
            "title",
            "context",
            "inferred_satisfaction",
            "satisfaction_reason",
            "created_at",
        ):
            value = event.get(key)
            if value not in (None, ""):
                compact[key] = value

        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            compact_metadata = {
                key: value
                for key, value in metadata.items()
                if key in _FEEDBACK_ANALYSIS_METADATA_KEYS and value not in (None, "")
            }
            if compact_metadata:
                compact["metadata"] = compact_metadata
        return compact

    def record_immediate_feedback_cognition(
        self,
        *,
        feedback_type: str,
        title: str,
        note: str = "",
    ) -> None:
        """Record one lightweight cognition update from a single strong feedback.

        This path is intentionally cheap: it only appends a short cognition update
        for UI visibility and does not trigger preference/profile rebuilds.
        """
        normalized_feedback = feedback_type.strip().lower()
        summary = ""
        kind = ""
        impact = ""
        reasoning = ""
        evidence = ""
        context_line = ""
        if normalized_feedback == "comment" and note.strip():
            kind = "profile_shift"
            title_text = title.strip()
            if title_text:
                summary = f"阿B 刚记下了你对《{title_text}》的评论。"
                evidence = f"你评论《{title_text}》时说：{note.strip()}"
                context_line = f"来自：《{title_text}》"
            else:
                summary = f"阿B 刚记下了：{note.strip()}"
                evidence = note.strip()
                context_line = "来自：这次推荐反馈"
            impact = "画像会结合评论内容判断这是喜欢、不喜欢还是补充说明，不会默认当成正向偏好。"
            reasoning = "这属于一条中性直接反馈，先记作方向修正，不直接重写整张画像。"
        elif normalized_feedback == "dislike":
            note_text = note.strip()
            generic_dislike_notes = {"太浅了", "不喜欢", "一般", "太水了", "没意思"}
            topic = (
                title.strip() if not note_text or note_text in generic_dislike_notes else note_text
            )
            if topic:
                kind = "dislike_added"
                summary = f"阿B 记住了：像“{topic}”这种内容你大概率会划走。"
                impact = "画像里的避雷方向会更明确，后面会更主动绕开这类内容。"
                reasoning = "这是一次明确负反馈，先把这个方向记成近期避雷。"
                evidence = note_text or title.strip()
                context_line = self._build_feedback_context_line(title)
        elif normalized_feedback == "like":
            title_text = title.strip()
            if title_text:
                kind = "interest_added"
                summary = f"阿B 记住了：像《{title_text}》这一路你大概率会继续想看。"
                impact = "画像里对这类方向的偏好会更明确，后面会更愿意继续补。"
                reasoning = "这是一次明确正反馈，先把这个方向记成近期偏好强化。"
                evidence = note.strip() or title_text
                context_line = self._build_feedback_context_line(title)
        else:
            return

        if not summary:
            return

        updates = self._memory.load_cognition_updates()
        if any(
            str(item.get("summary", "")).strip() == summary
            for item in updates
            if isinstance(item, dict)
        ):
            return
        updates.insert(
            0,
            {
                "id": f"cognition-{uuid4()}",
                "kind": kind,
                "summary": summary,
                "impact": impact,
                "reasoning": reasoning,
                "evidence": evidence,
                "context_line": context_line or "基于最近几条相关内容",
                "confidence": 0.82 if kind == "dislike_added" else 0.84,
                "created_at": datetime.now().isoformat(),
                "source": "feedback",
                "source_label": self._build_source_label("feedback"),
                "expand_hint": self._build_expand_hint(
                    impact=impact,
                    reasoning=reasoning,
                    evidence=evidence,
                ),
                "notified": False,
            },
        )
        self._memory.save_cognition_updates(updates)

    def _record_immediate_dialogue_cognition(
        self,
        candidates: list[dict[str, object]],
    ) -> None:
        """Record one lightweight cognition update from a single strong chat signal."""
        updates = self._memory.load_cognition_updates()
        changed = False
        for candidate in candidates:
            if not self._candidate_ready_for_immediate_dialogue_cognition(candidate):
                continue
            (
                summary,
                kind,
                impact,
                reasoning,
                evidence,
                context_line,
            ) = self._build_immediate_dialogue_cognition(candidate)
            if not summary:
                continue
            if any(
                str(item.get("summary", "")).strip() == summary
                for item in updates
                if isinstance(item, dict)
            ):
                continue
            updates.insert(
                0,
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": kind,
                    "summary": summary,
                    "impact": impact,
                    "reasoning": reasoning,
                    "evidence": evidence,
                    "context_line": context_line,
                    "confidence": round(self._to_float(candidate.get("confidence", 0.0)), 4),
                    "created_at": datetime.now().isoformat(),
                    "source": "chat",
                    "source_label": self._build_source_label("chat"),
                    "expand_hint": self._build_expand_hint(
                        impact=impact,
                        reasoning=reasoning,
                        evidence=evidence,
                    ),
                    "notified": False,
                },
            )
            changed = True
        if changed:
            self._memory.save_cognition_updates(updates)

    async def generate_awareness_note(self) -> str:
        """Generate a daily awareness note.

        The awareness note captures what the agent has observed about
        the user's recent behavior patterns, mood changes, and interest shifts.

        Returns:
            Natural language awareness note.
        """
        events = self._memory.query_events(limit=50)
        notes = await self._awareness_analyzer.analyze(
            events=events,
            preference=self._memory.get_layer("preference").data,
            soul_profile=self._memory.get_layer("soul").data,
        )
        if not notes:
            return ""
        merged = self._awareness_analyzer.merge_notes(self._load_awareness_notes(), notes)
        self._save_awareness_notes(merged)
        return notes[0].observation

    async def generate_insight(self) -> str:
        """Generate or update insight hypotheses.

        Insights are deeper interpretations of user behavior:
        - Why they do what they do
        - What psychological needs are being met
        - What latent interests might exist

        Returns:
            Natural language insight.
        """
        awareness_notes = self._load_awareness_notes()
        insights = await self._insight_analyzer.analyze(
            awareness_notes=awareness_notes,
            preference=self._memory.get_layer("preference").data,
            soul_profile=self._memory.get_layer("soul").data,
        )
        if not insights:
            return ""
        merged = self._insight_analyzer.merge_insights(self._load_insights(), insights)
        self._save_insights(merged)
        return insights[0].hypothesis

    def _load_awareness_notes(self) -> list[AwarenessNote]:
        layer_data = self._memory.get_layer("awareness").data
        notes = layer_data.get("notes", [])
        return [awareness_note_from_dict(item) for item in notes if isinstance(item, dict)]

    def _persist_init_cognition_drafts(self, context: dict[str, Any]) -> None:
        """Write init's awareness/insight drafts into the long-term layers.

        These drafts used to live only in ``_init_cognition_context``: they
        shaped the first portrait and were then discarded, and because init's
        history never reached the event table the cognition cycle could not
        re-derive them either. The practical cost was a brand-new install whose
        待聊 list was empty — the system had just formed concrete hypotheses
        about the user and asked none of them.

        Drafts are merged through the same paths a regular cognition pass uses,
        so dedup, lifecycle and user verdicts behave identically. Awareness
        notes cite the events init recorded this run when available, and are
        flagged approximate because the model attributed per round, not per
        note. Best-effort: a failure here must not fail init.
        """
        if not isinstance(context, dict) or not context:
            return
        try:
            source_event_ids: list[int] = []
            database = self._ledger_database
            if database is not None:
                try:
                    source_event_ids = [
                        int(row["id"])
                        for row in database.conn.execute(
                            "SELECT id FROM events ORDER BY id DESC LIMIT ?",
                            (_INIT_DRAFT_EVIDENCE_CAP,),
                        )
                    ]
                except Exception:
                    logger.debug("init draft evidence lookup failed", exc_info=True)
            raw_awareness = [
                item for item in _as_dict_list(context.get("awareness")) if item.get("observation")
            ]
            if raw_awareness:
                notes = [
                    AwarenessNote(
                        date=str(item.get("date", "") or "init"),
                        observation=str(item.get("observation", "")).strip(),
                        trend=str(item.get("trend", "")).strip(),
                        emotion_guess=str(item.get("emotion_guess", "")).strip(),
                        note_id=uuid4().hex[:12],
                        source_event_ids=list(source_event_ids),
                        source_event_ids_approximate=bool(source_event_ids),
                    )
                    for item in raw_awareness
                ]
                merged_notes = self._awareness_analyzer.merge_notes(
                    self._load_awareness_notes(), notes
                )
                self._save_awareness_notes(merged_notes)
            raw_insights = [
                item for item in _as_dict_list(context.get("insights")) if item.get("hypothesis")
            ]
            if raw_insights:
                hypotheses = [
                    InsightHypothesis(
                        hypothesis=str(item.get("hypothesis", "")).strip(),
                        evidence=_init_draft_evidence(item.get("evidence")),
                        confidence=_clamp_init_draft_confidence(item.get("confidence")),
                        validated=False,
                        created_at=datetime.now().date().isoformat(),
                    )
                    for item in raw_insights
                ]
                merged_insights = self._insight_analyzer.merge_insights(
                    self._load_insights(), hypotheses
                )
                self._save_insights(merged_insights)
            self._ledger.record(
                write_point="init_cognition_persist",
                source="init",
                before={},
                after={"awareness": len(raw_awareness), "insights": len(raw_insights)},
                source_refs=[f"events:{len(source_event_ids)}"],
            )
        except Exception:
            logger.warning("init cognition drafts were not persisted", exc_info=True)

    def _save_awareness_notes(self, notes: list[AwarenessNote]) -> None:
        layer = self._memory.get_layer("awareness")
        layer.data.clear()
        layer.data.update({"notes": [awareness_note_to_dict(item) for item in notes]})
        layer.save()

    def _load_insights(self) -> list[InsightHypothesis]:
        layer_data = self._memory.get_layer("insight").data
        hypotheses = layer_data.get("hypotheses", [])
        return [insight_hypothesis_from_dict(item) for item in hypotheses if isinstance(item, dict)]

    def _save_insights(self, insights: list[InsightHypothesis]) -> None:
        # Enforce production-stage deduplication at every persistence boundary.
        # Exact same-state duplicates and same-state near-duplicates collapse;
        # confirmed/rejected/unjudged variants remain separate.
        insights = InsightAnalyzer.dedupe_hypotheses(insights)
        layer = self._memory.get_layer("insight")
        layer.data.clear()
        layer.data.update({"hypotheses": [insight_hypothesis_to_dict(item) for item in insights]})
        layer.save()

    @staticmethod
    def _hypothesis_auto_validated(item: InsightHypothesis, *, now: datetime | None = None) -> bool:
        """Whether behaviour alone has earned this hypothesis deep influence.

        See the ``_AUTO_VALIDATE_*`` constants for the bar and its calibration.
        A user verdict of any kind disqualifies: "rejected" blocks permanently,
        and "confirmed" already travels the validated path.
        """
        if item.validated or item.user_verdict:
            return False
        if item.confidence < _AUTO_VALIDATE_MIN_CONFIDENCE:
            return False
        if len([e for e in item.evidence if str(e).strip()]) < _AUTO_VALIDATE_MIN_EVIDENCE:
            return False
        try:
            created = datetime.fromisoformat(str(item.created_at))
        except (TypeError, ValueError):
            return False  # undated == unproven tenure
        if item.confidence >= _AUTO_VALIDATE_FAST_MIN_CONFIDENCE:
            # Near-certainty skips the tenure wait; every other guard above
            # (evidence, no user verdict) has already been enforced.
            return True
        reference = now or datetime.now()
        if created.tzinfo is not None and reference.tzinfo is None:
            reference = reference.astimezone(created.tzinfo)
        elif created.tzinfo is None and reference.tzinfo is not None:
            created = created.astimezone(reference.tzinfo)
        age = reference - created
        return age >= timedelta(days=_AUTO_VALIDATE_MIN_AGE_DAYS)

    def _rebuild_active_insights(self) -> list[dict[str, object]]:
        """Insight dicts eligible to shape a soul rebuild (spec invariant 3 / F1).

        Two doors in: user-confirmed (validated, confidence >= 0.75) and
        behaviour-earned autonomy (``_hypothesis_auto_validated`` — stricter
        bar, never available to anything the user rejected). Everything else is
        filtered out, so a reject's next rebuild squeezes the old conclusion
        out instead of leaving it forever.
        """
        return [
            insight_hypothesis_to_dict(item)
            for item in self._load_insights()
            if (item.validated and item.confidence >= _REBUILD_MIN_CONFIDENCE)
            or self._hypothesis_auto_validated(item)
        ]

    # -- Pending confirmed-hypotheses rebuild state machine (spec r3/F3) -------

    def _rebuild_state_path(self) -> Path | None:
        data_dir = getattr(self._memory, "_data_dir", None)
        if data_dir is None:
            return None
        return Path(data_dir) / "memory" / "rebuild_pending_state.json"

    def _load_rebuild_state(self) -> dict[str, Any]:
        path = self._rebuild_state_path()
        if path is None or not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_rebuild_state(self, state: dict[str, Any]) -> None:
        path = self._rebuild_state_path()
        if path is None:
            return
        tmp_path = path.with_name(f"{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(path)
        except (OSError, TypeError, ValueError):
            logger.warning("Failed to save rebuild pending state", exc_info=True)
            raise
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to clean rebuild pending temp file: %s",
                    tmp_path,
                    exc_info=True,
                )

    async def _mark_rebuild_pending(self, trigger_refs: list[str]) -> None:
        """Set/refresh the pending marker (confirm & reject single point, inv 4).

        Replaying an existing trigger is a no-op so it cannot extend debounce or
        erase a bounded-retry attempt.  A genuinely new trigger re-stamps
        ``set_at`` and resets ``retry_count`` — "new evidence reopens" — while
        merging its ref with any still-pending ones.
        """
        async with self._rebuild_pending_lock:
            state = self._load_rebuild_state()
            existing = state.get("pending")
            refs = (
                [ref for ref in existing.get("trigger_refs", []) if isinstance(ref, str) and ref]
                if isinstance(existing, dict) and isinstance(existing.get("trigger_refs"), list)
                else []
            )
            seen_auto_refs = (
                [
                    ref
                    for ref in state.get("auto_hypothesis_trigger_refs", [])
                    if isinstance(ref, str) and ref.startswith(_AUTO_HYPOTHESIS_REF_PREFIX)
                ]
                if isinstance(state.get("auto_hypothesis_trigger_refs"), list)
                else []
            )
            has_new_trigger = False
            has_new_auto_ref = False
            for ref in trigger_refs:
                if ref.startswith(_AUTO_HYPOTHESIS_REF_PREFIX):
                    if ref in seen_auto_refs:
                        continue
                    seen_auto_refs.append(ref)
                    has_new_auto_ref = True
                if ref and ref not in refs:
                    refs.append(ref)
                    has_new_trigger = True
            if isinstance(existing, dict) and not has_new_trigger:
                if has_new_auto_ref:
                    state["auto_hypothesis_trigger_refs"] = seen_auto_refs
                    self._save_rebuild_state(state)
                return
            if not refs:
                return
            state["pending"] = {
                "set_at": datetime.now().isoformat(),
                "trigger_refs": refs,
                "retry_count": 0,
            }
            if has_new_auto_ref:
                state["auto_hypothesis_trigger_refs"] = seen_auto_refs
            self._save_rebuild_state(state)

    async def run_pending_rebuild_if_due(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Run the debounced gated confirmed-hypotheses rebuild when due (inv 4).

        Triggered by the 12h cognition loop and by the next dialogue-learning /
        feedback-batch pass. Debounced by ``_DEEP_REBUILD_DEBOUNCE_HOURS``.
        Clear-marker semantics (spec invariant 4):

        - gate accept + build ok → clear the marker.
        - gate downgrade/reject that is NOT an error (``is_error=False``) → this
          batch is abandoned: clear the marker + record ``last_gate_refusal``. A
          later confirm/reject re-opens it (no infinite retry).
        - gate error OR build exception (``is_error=True``) → keep the marker,
          bump ``retry_count``; after ``_REBUILD_MAX_RETRIES`` clear + WARNING.

        A concurrent re-mark during the build is reconciled by comparing
        ``set_at`` (compare-and-swap): if it changed, the fresh marker is left
        intact rather than clobbered by this run's outcome.
        """
        current = now or datetime.now()
        # Behaviour-earned hypotheses join the same debounced pending machine
        # user confirmations use — same gate, same ledger, same retry bounds.
        # _mark_rebuild_pending is idempotent for already-known refs, so
        # re-scanning at every checkpoint cannot extend the debounce.
        try:
            auto_refs = sorted(
                f"{_AUTO_HYPOTHESIS_REF_PREFIX}"
                f"{hashlib.sha1(item.hypothesis.encode('utf-8')).hexdigest()[:12]}"
                for item in self._load_insights()
                if self._hypothesis_auto_validated(item, now=current)
            )
            if auto_refs:
                await self._mark_rebuild_pending(auto_refs)
        except Exception:
            logger.debug("auto-validated hypothesis scan failed", exc_info=True)

        async with self._rebuild_pending_lock:
            if self._rebuild_running:
                return {"ran": False, "reason": "in_progress"}
            state = self._load_rebuild_state()
            pending = state.get("pending")
            if not isinstance(pending, dict):
                return {"ran": False, "reason": "not_pending"}
            set_at = self._parse_iso(pending.get("set_at"))
            if set_at is None or (current - set_at) < timedelta(hours=_DEEP_REBUILD_DEBOUNCE_HOURS):
                return {"ran": False, "reason": "debounced"}
            started_set_at = str(pending.get("set_at", ""))
            trigger_refs = [str(r) for r in pending.get("trigger_refs", []) if isinstance(r, str)]
            retry_count = int(pending.get("retry_count", 0) or 0)
            self._rebuild_running = True

        # The long LLM build runs WITHOUT the lock so a concurrent confirm/reject
        # can re-mark pending (reconciled below via compare-and-swap on set_at).
        try:
            outcome = await self._execute_pending_rebuild(trigger_refs)
        except Exception:
            logger.exception("pending rebuild dispatch failed")
            outcome = "error"

        async with self._rebuild_pending_lock:
            self._rebuild_running = False
            state = self._load_rebuild_state()
            pending = state.get("pending")
            if not isinstance(pending, dict) or str(pending.get("set_at", "")) != started_set_at:
                # A newer confirm/reject reopened the marker mid-build — leave it.
                return {"ran": True, "outcome": outcome, "superseded": True}
            if outcome == "accept":
                state["pending"] = None
            elif outcome == "refusal":
                state["pending"] = None
                state["last_gate_refusal"] = {
                    "at": current.isoformat(),
                    "trigger_refs": trigger_refs,
                }
            else:  # error
                retry_count += 1
                if retry_count >= _REBUILD_MAX_RETRIES:
                    logger.warning(
                        "pending rebuild exceeded retry budget (%d); clearing marker",
                        _REBUILD_MAX_RETRIES,
                    )
                    state["pending"] = None
                else:
                    pending["retry_count"] = retry_count
                    state["pending"] = pending
            self._save_rebuild_state(state)
            return {"ran": True, "outcome": outcome, "retry_count": retry_count}

    async def _execute_pending_rebuild(self, trigger_refs: list[str]) -> str:
        """Gate + run one confirmed-hypotheses rebuild. Returns the outcome tag.

        ``accept`` (rebuilt), ``refusal`` (gate downgrade/reject, real verdict),
        or ``error`` (gate is_error, or a build exception).
        """
        preference = dict(self._memory.get_layer("preference").data)
        existing_profile = dict(self._memory.get_layer("soul").data)
        insights = self._load_insights()
        confirmed = [
            item
            for item in insights
            if item.validated and item.confidence >= _REBUILD_MIN_CONFIDENCE
        ]
        auto_validated = [item for item in insights if self._hypothesis_auto_validated(item)]
        context: dict[str, object] = {
            "confirmed_hypotheses": [item.hypothesis for item in confirmed][
                :_REBUILD_CONTEXT_HYPOTHESIS_CAP
            ],
            "auto_validated_hypotheses": [item.hypothesis for item in auto_validated][
                :_REBUILD_CONTEXT_HYPOTHESIS_CAP
            ],
            "trigger_refs": trigger_refs,
        }
        source_refs = trigger_refs or ["rebuild_pending"]
        decision = await self._gate_soul_rebuild(
            trigger=_REBUILD_TRIGGER_CONFIRMED_HYPOTHESES,
            existing_preference=preference,
            updated_preference=preference,
            source_refs=source_refs,
            context=context,
        )
        if decision.blocks:
            return "error" if decision.is_error else "refusal"
        try:
            with self._ledger.action(
                write_point="hypotheses_soul_rebuild",
                source="hypotheses",
                before=existing_profile,
                source_refs=source_refs,
            ) as _entry:
                legacy_profile = await self._profile_builder.build(
                    history=[],
                    preference=preference,
                    awareness_notes=[
                        awareness_note_to_dict(item) for item in self._load_awareness_notes()
                    ],
                    active_insights=self._rebuild_active_insights(),
                )
                profile = OnionProfile.from_legacy(legacy_profile)
                profile.populate_from_flat_preference(preference)
                soul_layer = self._memory.get_layer("soul")
                soul_layer.data.clear()
                soul_layer.data.update(profile.to_dict())
                soul_layer.save()
                _entry.after = dict(soul_layer.data)
            self._memory.sync_profile_files(profile)
            return "accept"
        except Exception:
            logger.exception("Failed to rebuild soul profile from confirmed hypotheses")
            return "error"

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    async def replay_confusion_dialogue_attributions(self) -> int:
        """Enumerate replay candidates read-only and submit the dedicated kind."""
        queue = self._require_dialogue_settlement_queue()
        if asyncio.current_task() is queue.worker_task:
            from .dialogue_learn_queue import DialogueSettlementReentryError

            raise DialogueSettlementReentryError(
                "confusion attribution replay hook cannot submit from the worker"
            )
        pending = self._confusion_manager.pending_dialogue_replays()
        if not pending:
            return 0
        admitted: list[asyncio.Future[DialogueJobResult]] = []
        for row in pending:
            try:
                confusion_id = int(row.get("confusion_id", 0))
            except (TypeError, ValueError):
                continue
            confusion = self._confusion_manager.get(confusion_id)
            if confusion is None:
                continue
            has_replay_queue = bool(confusion.replay_queue)
            replay_head = confusion.replay_queue[0] if has_replay_queue else {}
            replay_id = str(
                replay_head.get("replay_id")
                or replay_head.get("turn_id")
                or row.get("turn_id")
                or ""
            ).strip()
            turn_id = str(row.get("turn_id") or replay_head.get("turn_id") or "").strip()
            if not replay_id:
                continue
            active_anchor = self._dialogue_anchor_manager.current()
            needs_anchor = bool(
                not has_replay_queue
                and confusion.status == "clarifying"
                and (
                    active_anchor is None
                    or (
                        active_anchor.kind == "confusion" and active_anchor.ref == str(confusion_id)
                    )
                )
            )
            payload: dict[str, object] = {
                "confusion_id": confusion_id,
                "turn_id": turn_id,
                "replay_id": replay_id,
                "ask_turn_id": str(row.get("ask_turn_id", "")),
                "subject_id": str(row.get("subject_id", "")),
                "subject_title": str(row.get("subject_title", "")),
                "message": str(row.get("message", "")),
                "reply": str(row.get("reply", "")),
                "has_replay_queue": has_replay_queue,
                "needs_anchor": needs_anchor,
                "target_kind": "confusion",
                "target_ref": str(confusion_id),
                "producer_source": "cognition_cycle",
            }
            job = queue.submit(
                DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY,
                payload,
                completion=True,
            )
            if job is not None and job.completion is not None:
                admitted.append(job.completion)

        processed = 0
        for completion_future in admitted:
            try:
                completion = await asyncio.shield(completion_future)
            except Exception:
                logger.warning("confusion attribution replay job failed", exc_info=True)
                continue
            if completion.outcome == "applied":
                processed += 1
        return processed

    async def _dispatch_confusion_attribution_replay(
        self,
        job: DialogueJob,
    ) -> DialogueDispatchResult | DialogueJobResult:
        """Prepare one replay, resolving any builder before its async effects."""
        self._require_dialogue_settlement_worker()
        confusion_id = int(str(job.payload.get("confusion_id", 0)))
        turn_id = str(job.payload.get("turn_id", "")).strip()
        ref = str(confusion_id)
        if self._confusion_replay_receipt_exists(turn_id):
            result = DialogueJobResult(outcome="already_terminal")
            if job.owned_anchor_reservation_id is None:
                return result
            return DialogueDispatchResult(
                result=result,
                anchor_terminal=AnchorMutationTerminal.already_terminal(
                    self._dialogue_anchor_actual_state(kind="confusion", ref=ref),
                ),
            )

        has_replay_queue = bool(job.payload.get("has_replay_queue", False))
        if has_replay_queue:
            return await self._apply_confusion_attribution_replay_effect(
                job=job,
                anchor=None,
            )

        confusion = self._confusion_manager.get(confusion_id)
        if confusion is None or confusion.status != "clarifying":
            result = DialogueJobResult(outcome="already_terminal")
            if job.owned_anchor_reservation_id is None:
                return result
            return DialogueDispatchResult(
                result=result,
                anchor_terminal=AnchorMutationTerminal.already_terminal(
                    self._dialogue_anchor_actual_state(kind="confusion", ref=ref),
                ),
            )

        if job.owned_anchor_reservation_id is not None:
            existing = self._dialogue_anchor_manager.current()
            established = self._dialogue_anchor_manager.establish(
                kind="confusion",
                ref=ref,
                origin_turn_id=(
                    confusion.ask_turn_id or str(job.payload.get("ask_turn_id", "")) or turn_id
                ),
                entry=ENTRY_CONFUSION_PROMPT,
            )
            terminal = (
                AnchorMutationTerminal.no_op(
                    self._dialogue_anchor_actual_state(kind="confusion", ref=ref),
                )
                if (
                    existing is not None
                    and existing.kind == established.kind
                    and existing.ref == established.ref
                )
                else AnchorMutationTerminal.persisted(
                    kind=established.kind,
                    ref=established.ref,
                    generation=established.generation,
                )
            )

            async def _after_anchor_resolution() -> DialogueJobResult:
                return await self._apply_confusion_attribution_replay_effect(
                    job=job,
                    anchor=established,
                )

            return DialogueDispatchResult(
                result=DialogueJobResult(outcome="prepared"),
                anchor_terminal=terminal,
                followup=_after_anchor_resolution,
            )

        snapshot = job.effective_anchor_snapshot
        anchor = (
            self._dialogue_anchor_manager.validate_snapshot(
                snapshot.ref,
                snapshot.generation,
            )
            if isinstance(snapshot, AnchorPersisted)
            else None
        )
        if anchor is None or anchor.kind != "confusion" or anchor.ref != ref:
            return DialogueJobResult(outcome="skipped_foreign_anchor")
        return await self._apply_confusion_attribution_replay_effect(
            job=job,
            anchor=anchor,
        )

    async def _apply_confusion_attribution_replay_effect(
        self,
        *,
        job: DialogueJob,
        anchor: DialogueAnchor | None,
    ) -> DialogueJobResult:
        """Analyze/apply one replay identity without submitting a nested job."""
        self._require_dialogue_settlement_worker()
        confusion_id = int(str(job.payload.get("confusion_id", 0)))
        turn_id = str(job.payload.get("turn_id", "")).strip()
        replay_id = str(job.payload.get("replay_id", "")).strip()
        if self._confusion_replay_receipt_exists(turn_id):
            return DialogueJobResult(outcome="already_terminal")
        confusion = self._confusion_manager.get(confusion_id)
        if confusion is None:
            return DialogueJobResult(outcome="already_terminal")

        if bool(job.payload.get("has_replay_queue", False)):
            if confusion.replay_queue:
                head = confusion.replay_queue[0]
                head_id = str(head.get("replay_id") or head.get("turn_id") or "")
                if head_id != replay_id:
                    return DialogueJobResult(outcome="already_terminal")
            settlement_reader = getattr(
                self._ledger_database,
                "get_card_settlement",
                None,
            )
            settlement = (
                settlement_reader(str(confusion_id)) if callable(settlement_reader) else None
            )
            settlement_payload = settlement.get("payload") if isinstance(settlement, dict) else None
            if (
                isinstance(settlement, dict)
                and int(settlement.get("applied", 0)) == 0
                and isinstance(settlement_payload, dict)
                and str(settlement_payload.get("kind", "")) == "confusion"
            ):
                try:
                    generation = max(
                        0,
                        int(settlement_payload.get("anchor_generation", 0)),
                    )
                except (TypeError, ValueError):
                    generation = 0
                snapshot: AnchorAdmissionSnapshot = (
                    AnchorPersisted(
                        kind="confusion",
                        ref=str(confusion_id),
                        generation=generation,
                    )
                    if generation > 0
                    else AnchorAbsent(
                        target_kind="confusion",
                        target_ref=str(confusion_id),
                        tombstone_epoch=1,
                    )
                )
                result = await self._apply_confusion_answer_settlement(
                    ref=str(confusion_id),
                    confusion_id=confusion_id,
                    interpretation=str(settlement_payload.get("interpretation", "")),
                    note=str(settlement_payload.get("note", "")),
                    turn_id=str(settlement.get("turn_id", turn_id)),
                    source=str(settlement_payload.get("source", "recovery")),
                    anchor_snapshot=snapshot,
                )
                return DialogueJobResult(
                    outcome=(
                        "retry_pending" if result.get("outcome") == "processing" else "applied"
                    ),
                )
            terminal = self._confusion_manager.retry_anchor_settlements(confusion_id)
            if terminal is None:
                return DialogueJobResult(outcome="retry_pending")
            current = self._dialogue_anchor_manager.current()
            if (
                current is not None
                and current.kind == "confusion"
                and current.ref == str(confusion_id)
            ):
                self._dialogue_anchor_manager.release(
                    reason="settled",
                    expected_generation=current.generation,
                )
            return DialogueJobResult(outcome="applied")

        if confusion.status != "clarifying" or anchor is None:
            return DialogueJobResult(outcome="already_terminal")
        anchor_context, anchor_texts = self._build_dialogue_anchor_context(anchor)
        label = str(job.payload.get("subject_title") or job.payload.get("subject_id") or "这个方向")
        user_message = f"[关于我有点困惑的「{label}」的澄清] {str(job.payload.get('message', ''))}"
        try:
            extract_result = await self._dialogue_insight_analyzer.extract(
                user_message=user_message,
                assistant_reply=str(job.payload.get("reply", "")),
                core_memory=self._memory.get_core_memory(),
                active_list=self._build_dialogue_active_list()[0],
                anchor=anchor_context,
            )
        except DialogueInsightAnalysisError:
            logger.warning(
                "confusion dialogue attribution replay failed analysis: turn_id=%s",
                turn_id,
                exc_info=True,
            )
            return DialogueJobResult(outcome="retry_pending")
        raw_decision = extract_result.get("anchor")
        if not isinstance(raw_decision, dict):
            logger.warning(
                "confusion dialogue attribution replay missing decision: turn_id=%s",
                turn_id,
            )
            return DialogueJobResult(outcome="retry_pending")
        if (
            self._dialogue_anchor_manager.validate_snapshot(
                anchor.ref,
                anchor.generation,
            )
            is None
        ):
            return DialogueJobResult(outcome="stale")
        outcome = await self._process_dialogue_anchor_decision(
            anchor=anchor,
            anchor_texts=anchor_texts,
            decision=raw_decision,
            turn_id=turn_id,
        )
        if outcome in {"kept_invalid", "kept_failed", "queued_failed", "stale"}:
            return DialogueJobResult(outcome="retry_pending")
        return DialogueJobResult(outcome="applied")

    def _confusion_replay_receipt_exists(self, turn_id: str) -> bool:
        if not turn_id or self._ledger_database is None:
            return False
        get_turn = getattr(self._ledger_database, "get_chat_turn", None)
        row = get_turn(turn_id) if callable(get_turn) else None
        raw_payload = row.get("payload", {}) if isinstance(row, dict) else {}
        return bool(
            isinstance(raw_payload, dict)
            and int(raw_payload.get("confusion_anchor_processed", 0) or 0) == 1
        )

    def _dialogue_anchor_actual_state(
        self,
        *,
        kind: str,
        ref: str,
    ) -> AnchorPersisted | AnchorAbsent:
        current = self._dialogue_anchor_manager.current()
        if current is not None and current.kind == kind and current.ref == ref:
            return AnchorPersisted(
                kind=current.kind,
                ref=current.ref,
                generation=current.generation,
            )
        return AnchorAbsent(
            target_kind=kind,
            target_ref=ref,
            tombstone_epoch=1,
        )

    def _build_dialogue_anchor_context(
        self,
        anchor: DialogueAnchor,
    ) -> tuple[dict[str, object], list[str]]:
        """Resolve an anchor ref to the object text injected into the existing LLM call."""
        texts: list[str] = []
        if anchor.kind == "hypothesis":
            hypotheses = [item.hypothesis for item in self._load_insights() if item.hypothesis]
            hypothesis_by_ref = build_hash8_map(hypotheses)
            hypothesis = hypothesis_by_ref.get(anchor.ref, "")
            if hypothesis:
                texts.append(hypothesis)
            else:
                logger.warning("dialogue hypothesis anchor ref no longer resolves: %s", anchor.ref)
        else:
            try:
                confusion_id = int(anchor.ref.rsplit(":", maxsplit=1)[-1])
            except ValueError:
                confusion_id = 0
            confusion = self._confusion_manager.get(confusion_id) if confusion_id else None
            if confusion is not None:
                texts.extend(
                    text
                    for text in (
                        confusion.topic.strip(),
                        confusion.observation.strip(),
                        confusion.interpretation.strip(),
                    )
                    if text
                )
            else:
                logger.warning("dialogue confusion anchor ref no longer resolves: %s", anchor.ref)
        context = {
            "kind": anchor.kind,
            "ref": anchor.ref,
            "generation": anchor.generation,
            "text": texts[0] if texts else "",
            "object_texts": texts,
            "ambiguous_count": anchor.ambiguous_count,
        }
        return context, texts

    def _filter_anchor_overlap_candidates(
        self,
        candidates: list[dict[str, object]],
        anchor_texts: list[str],
    ) -> list[dict[str, object]]:
        """Drop candidates that duplicate anchored content (second defence)."""
        if not anchor_texts:
            return candidates
        kept: list[dict[str, object]] = []
        for candidate in candidates:
            content = str(candidate.get("content", "")).strip()
            overlap = max(
                (_dialogue_anchor_jaccard(content, anchor_text) for anchor_text in anchor_texts),
                default=0.0,
            )
            if overlap >= _ANCHOR_CANDIDATE_JACCARD_THRESHOLD:
                logger.warning(
                    "dialogue candidate overlaps dialogue anchor (jaccard=%.3f); dropped: %s",
                    overlap,
                    content[:80],
                )
                continue
            kept.append(candidate)
        return kept

    async def _process_dialogue_anchor_decision(
        self,
        *,
        anchor: DialogueAnchor,
        anchor_texts: list[str],
        decision: dict[str, object],
        turn_id: str,
        binding_provenance: Mapping[str, object] | None = None,
    ) -> str:
        """Apply one matrix-validated anchor relation without another LLM call."""
        raw_relation = decision.get("relation")
        relation = raw_relation.strip() if isinstance(raw_relation, str) else ""
        allowed = _ANCHOR_RELATIONS_BY_KIND.get(anchor.kind, frozenset())
        all_relations = frozenset().union(*_ANCHOR_RELATIONS_BY_KIND.values())
        if relation not in all_relations:
            logger.warning("dialogue anchor decision dropped in engine: relation=%r", raw_relation)
            return "kept_invalid"
        if relation not in allowed:
            logger.warning(
                "dialogue anchor relation outside kind matrix in engine: kind=%s relation=%s; "
                "coercing to unrelated",
                anchor.kind,
                relation,
            )
            relation = "unrelated"

        if relation == "unrelated":
            updated = self._dialogue_anchor_manager.note_relation(
                relation,
                expected_generation=anchor.generation,
            )
            if updated is None:
                current = self._dialogue_anchor_manager.current()
                if current is not None:
                    return "stale"
            if anchor.kind == "confusion":
                confusion_id = self._confusion_anchor_id(anchor)
                if confusion_id:
                    self._confusion_manager.record_anchor_relation_processed(
                        confusion_id,
                        relation=relation,
                        turn_id=turn_id,
                    )
            return "unrelated" if updated is not None else "released_unrelated"

        if relation == "ambiguous":
            updated = self._dialogue_anchor_manager.note_relation(
                relation,
                expected_generation=anchor.generation,
            )
            if updated is None:
                return "stale"
            if updated.ambiguous_count <= _ANCHOR_AMBIGUOUS_FOLLOW_UP_LIMIT:
                self._ledger.record(
                    write_point="anchor_follow_up",
                    source="dialogue_anchor",
                    after={"ambiguous_count": updated.ambiguous_count},
                    source_refs=[f"{anchor.kind}:{anchor.ref}"],
                    turn_id=turn_id,
                )
                if anchor.kind == "confusion":
                    confusion_id = self._confusion_anchor_id(anchor)
                    if confusion_id:
                        self._confusion_manager.record_anchor_relation_processed(
                            confusion_id,
                            relation=relation,
                            turn_id=turn_id,
                        )
                return "follow_up"
            if anchor.kind == "hypothesis":
                released = self._dialogue_anchor_manager.release(
                    reason="settled",
                    card_state="deferred",
                    expected_generation=anchor.generation,
                )
            else:
                confusion_id = self._confusion_anchor_id(anchor)
                terminal = (
                    self._confusion_manager.process_anchor_settlement(
                        confusion_id,
                        action="defer",
                        note="second ambiguous dialogue turn",
                        turn_id=turn_id,
                        anchor_generation=anchor.generation,
                    )
                    if confusion_id
                    else None
                )
                if terminal is None:
                    return "queued_failed"
                released = self._dialogue_anchor_manager.release(
                    reason="settled",
                    expected_generation=anchor.generation,
                )
            return "deferred" if released is not None else "stale"

        # Every valid non-ambiguous relation resets both counters before its
        # object-specific side effect. A failed side effect leaves a clean,
        # still-active anchor for the next turn.
        if (
            self._dialogue_anchor_manager.note_relation(
                relation,
                expected_generation=anchor.generation,
            )
            is None
        ):
            return "stale"
        if anchor.kind == "hypothesis":
            hypothesis = anchor_texts[0] if anchor_texts else ""
            if not hypothesis:
                logger.warning("dialogue hypothesis anchor has no resolvable object text")
                return "kept_invalid"
            if relation in {"support", "contradict"}:
                settlement_kwargs: dict[str, Any] = {
                    "ref": anchor.ref,
                    "hypothesis": hypothesis,
                    "requested_verdict": relation,
                    "turn_id": turn_id,
                    "source": "dialogue_anchor",
                    "anchor_snapshot": AnchorPersisted(
                        kind=anchor.kind,
                        ref=anchor.ref,
                        generation=anchor.generation,
                    ),
                }
                if binding_provenance is not None:
                    settlement_kwargs["provenance"] = binding_provenance
                result = await self._apply_hypothesis_settlement(**settlement_kwargs)
                if result.get("outcome") == "processing":
                    return "queued_failed"
                return str(result.get("state", "stale"))
            if relation == "revise":
                raw_derived = decision.get("derived")
                derived = _as_dict_list(raw_derived)
                settlement_kwargs = {
                    "ref": anchor.ref,
                    "hypothesis": hypothesis,
                    "requested_verdict": "revise",
                    "turn_id": turn_id,
                    "source": "dialogue_anchor",
                    "derived": derived,
                    "anchor_snapshot": AnchorPersisted(
                        kind=anchor.kind,
                        ref=anchor.ref,
                        generation=anchor.generation,
                    ),
                }
                if binding_provenance is not None:
                    settlement_kwargs["provenance"] = binding_provenance
                result = await self._apply_hypothesis_settlement(**settlement_kwargs)
                if result.get("outcome") == "processing":
                    return "queued_failed"
                if result.get("settlement_verdict") == "revised":
                    return "revised"
                return str(result.get("state", "stale"))
            return "kept_invalid"

        if relation != "answer":
            return "kept_invalid"
        interpretation = str(decision.get("interpretation", "")).strip()
        if interpretation not in {"real_interest", "proxy_behavior", "dismissed"}:
            logger.warning(
                "dialogue confusion answer has invalid interpretation=%r",
                interpretation,
            )
            return "kept_invalid"
        confusion_id = self._confusion_anchor_id(anchor)
        if not confusion_id:
            return "kept_invalid"
        settlement_kwargs = {
            "ref": anchor.ref,
            "confusion_id": confusion_id,
            "interpretation": interpretation,
            "note": "dialogue_anchor",
            "turn_id": turn_id,
            "source": "dialogue_anchor",
            "anchor_snapshot": AnchorPersisted(
                kind=anchor.kind,
                ref=anchor.ref,
                generation=anchor.generation,
            ),
        }
        if binding_provenance is not None:
            settlement_kwargs["provenance"] = binding_provenance
        result = await self._apply_confusion_answer_settlement(**settlement_kwargs)
        if result.get("outcome") == "processing":
            return "queued_failed"
        return "answered"

    async def _persist_anchor_derived_hypotheses(
        self,
        derived: list[dict[str, object]],
    ) -> list[str]:
        """Idempotently upsert revise-derived hypotheses and return marker refs."""
        existing = self._load_insights()
        by_text = {self._normalize_text(item.hypothesis): item for item in existing}
        trigger_refs: list[str] = []
        changed = False
        for item in derived:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            confidence = max(0.75, min(1.0, self._to_float(item.get("confidence", 0.0))))
            evidence = str(item.get("evidence", "")).strip()
            normalized = self._normalize_text(content)
            hypothesis = by_text.get(normalized)
            if hypothesis is None:
                hypothesis = InsightHypothesis(
                    hypothesis=content,
                    evidence=[evidence] if evidence else [],
                    confidence=round(confidence, 4),
                    validated=True,
                    created_at=datetime.now().isoformat(),
                    # The user supplied this wording themselves in the dialogue,
                    # so it counts as a user verdict — a later insight pass must
                    # not talk its confidence down.
                    user_verdict="confirmed",
                )
                existing.append(hypothesis)
                by_text[normalized] = hypothesis
                changed = True
            else:
                next_confidence = round(max(hypothesis.confidence, confidence), 4)
                if not hypothesis.validated:
                    hypothesis.validated = True
                    changed = True
                if hypothesis.user_verdict != "confirmed":
                    hypothesis.user_verdict = "confirmed"
                    changed = True
                if hypothesis.confidence != next_confidence:
                    hypothesis.confidence = next_confidence
                    changed = True
                if evidence and evidence not in hypothesis.evidence:
                    hypothesis.evidence.append(evidence)
                    changed = True
            trigger_refs.append(f"anchor_revise:{content[:60]}")
        if changed:
            self._save_insights(existing)
        return trigger_refs

    @staticmethod
    def _confusion_anchor_id(anchor: DialogueAnchor) -> int:
        try:
            return int(anchor.ref.rsplit(":", maxsplit=1)[-1])
        except ValueError:
            logger.warning("dialogue confusion anchor has malformed ref=%r", anchor.ref)
            return 0

    @staticmethod
    def _with_anchor_outcome(
        result: dict[str, object],
        outcome: str,
    ) -> dict[str, object]:
        if outcome:
            result["anchor_outcome"] = outcome
        return result

    def _stale_anchor_drop_result(
        self,
        *,
        anchor_ref: str,
        anchor_generation: int,
        turn_id: str,
        phase: str,
        context_digest: str = "",
    ) -> dict[str, object]:
        """Warn and audit one stale queued anchor result without side effects."""
        logger.warning(
            "stale dialogue anchor result discarded before side effects: "
            "phase=%s ref=%r generation=%s turn_id=%s",
            phase,
            anchor_ref,
            anchor_generation,
            turn_id,
        )
        self._ledger.record(
            write_point="anchor_stale_generation_drop",
            source=phase,
            before={"ref": anchor_ref, "generation": anchor_generation},
            after={"discarded": True},
            source_refs=[f"anchor:{anchor_ref}"],
            turn_id=turn_id,
        )
        result: dict[str, object] = {
            "event_logged": True,
            "candidate_count": 0,
            "preference_updated": False,
            "profile_rebuilt": False,
            "anchor_outcome": "stale",
        }
        if context_digest:
            result["binding_status"] = "stale"
            result["context_digest"] = context_digest
        return result

    def _merge_insight_candidates(
        self,
        existing_candidates: list[dict[str, object]],
        new_candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        merged = [dict(item) for item in existing_candidates if isinstance(item, dict)]
        for raw_candidate in new_candidates:
            kind = str(raw_candidate.get("kind", "")).strip() or "state"
            content = str(raw_candidate.get("content", "")).strip()
            if not content:
                continue
            normalized_content = self._normalize_text(content)
            existing = next(
                (
                    item
                    for item in merged
                    if self._normalize_text(str(item.get("content", ""))) == normalized_content
                    and str(item.get("kind", "")).strip() == kind
                ),
                None,
            )
            now = datetime.now().isoformat()
            confidence = self._to_float(raw_candidate.get("confidence", 0.0))
            evidence = str(raw_candidate.get("evidence", "")).strip()
            if existing is None:
                merged.append(
                    {
                        "id": str(uuid4()),
                        "kind": kind,
                        "content": content,
                        "confidence": max(0.0, min(1.0, round(confidence, 4))),
                        "evidence": evidence,
                        "occurrences": 1,
                        "confirmed": False,
                        "applied": False,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                continue
            existing["occurrences"] = self._to_int(existing.get("occurrences", 0)) + 1
            existing["confidence"] = max(
                self._to_float(existing.get("confidence", 0.0)),
                max(0.0, min(1.0, round(confidence, 4))),
            )
            if evidence:
                existing["evidence"] = evidence
            existing["updated_at"] = now
        return merged

    def _build_dialogue_active_list(
        self,
    ) -> tuple[dict[str, object], dict[str, str]]:
        """Assemble the settle-injection list (speculations / insights / confusions).

        Returns ``(active_list, insight_hash_map)`` where ``insight_hash_map``
        maps the injected insight hash key -> hypothesis text. Confusions are an
        empty list in Wave A (the confusion object lands in Wave B).
        """
        speculations: list[dict[str, object]] = []
        try:
            active_specs = self._speculator.get_active_speculations()
            # Cap at 10 (spec: activelist speculation ≤10) to bound the prompt.
            for spec in active_specs[:10]:
                domain = str(getattr(spec, "domain", "")).strip()
                if domain:
                    speculations.append({"domain": domain})
        except Exception:
            logger.debug("Failed to load active speculations for settles", exc_info=True)

        insight_hypotheses = [
            item.hypothesis for item in self._load_insights() if item.hypothesis.strip()
        ]
        insight_hash_map = build_hash8_map(insight_hypotheses)
        # Reverse map (hypothesis -> key) preserves injection order for the prompt.
        key_by_text = {text: key for key, text in insight_hash_map.items()}
        insights = [
            {"hash": key_by_text[text], "hypothesis": text}
            for text in insight_hypotheses
            if text in key_by_text
        ]

        confusions: list[dict[str, object]] = []
        try:
            for confusion in self._confusion_manager.list_active():
                confusions.append(
                    {
                        "id": str(confusion.id),
                        "topic": confusion.topic,
                        "observation": confusion.observation,
                    }
                )
        except Exception:
            logger.debug("Failed to load active confusions for settles", exc_info=True)

        active_list: dict[str, object] = {
            "speculations": speculations,
            "insights": insights,
            "confusions": confusions,
        }
        return active_list, insight_hash_map

    async def _process_dialogue_settles(
        self,
        *,
        settles: list[dict[str, object]],
        active_list: dict[str, object],
        insight_hash_map: dict[str, str],
        turn_id: str,
        admission_anchor_ref: str,
        admission_anchor_generation: int,
    ) -> None:
        """Settle active objects referenced by a chat turn (whitelist = injected).

        ``ref`` must appear in the round's injection list (spec §invariant 5);
        unknown refs are dropped with WARNING. Settling calls existing functions
        (speculation confirm/reject, insight feedback) and records a ledger row
        stamped with ``turn_id`` (idempotency observation key).
        """
        if admission_anchor_ref or admission_anchor_generation:
            raise RuntimeError(
                "Ordinary dialogue settles require the admission-time absent anchor state"
            )
        spec_domains = {
            str(item.get("domain", "")).strip()
            for item in _as_dict_list(active_list.get("speculations"))
        }
        confusion_ids = {
            str(item.get("id", "")).strip() for item in _as_dict_list(active_list.get("confusions"))
        }
        for settle in settles:
            kind = str(settle.get("kind", "")).strip()
            ref = str(settle.get("ref", "")).strip()
            verdict = str(settle.get("verdict", "")).strip()
            if not ref:
                continue
            if kind == "speculation":
                if ref not in spec_domains:
                    logger.warning("dialogue settle ref not in injected list: %s", ref)
                    continue
                try:
                    await self._apply_speculation_settlement(
                        ref=ref,
                        requested_verdict=verdict,
                        turn_id=turn_id,
                        source="chat",
                    )
                except Exception:
                    logger.exception("Failed to settle speculation %s", ref)
            elif kind == "insight":
                hypothesis = insight_hash_map.get(ref)
                if hypothesis is None:
                    logger.warning("dialogue settle ref not in injected list: %s", ref)
                    continue
                try:
                    await self._apply_hypothesis_settlement(
                        ref=ref,
                        hypothesis=hypothesis,
                        requested_verdict=verdict,
                        turn_id=turn_id,
                        source="chat",
                        anchor_snapshot=AnchorAbsent(
                            target_kind="hypothesis",
                            target_ref=ref,
                            tombstone_epoch=1,
                        ),
                    )
                except Exception:
                    logger.exception("Failed to settle insight %s", ref)
            elif kind == "confusion":
                if ref not in confusion_ids:
                    logger.warning("dialogue settle ref not in injected list: %s", ref)
                    continue
                try:
                    await self._apply_confusion_settlement(
                        ref=ref,
                        requested_verdict=verdict,
                        note="chat_settle",
                        turn_id=turn_id,
                        source="chat",
                        anchor_snapshot=AnchorAbsent(
                            target_kind="confusion",
                            target_ref=ref,
                            tombstone_epoch=1,
                        ),
                    )
                except Exception:
                    logger.exception("Failed to settle confusion %s", ref)
            else:
                logger.warning("dialogue settle dropped: unknown kind=%s", kind)

    async def replay_held_updates(self) -> dict[str, object]:
        """Rebase resolved-real-interest held updates into preference analysis.

        Wave B held-replay consumer (leftover wiring). A confusion resolved as
        ``real_interest`` leaves its held topic updates in the ``replaying``
        state with a receipt. This consumer feeds those held topics as evidence
        into the preference analyzer (rebase semantics — never a direct weight
        write), persists the result through the normal chokepoint (freeze + the
        interest fast line, which is not gated), then marks the replay
        ``applied``. Idempotent: once applied the items are no longer
        ``replaying``, so a second run is a no-op. A crash between the
        preference write and ``mark_replay_applied`` is reconciled to
        ``applied_unverified`` by :meth:`recover_replaying` at next startup.
        """
        pending = self._confusion_manager.pending_replays()
        if not pending:
            return {"replayed": 0, "confusions": 0}
        events: list[dict[str, object]] = []
        for confusion in pending:
            for held in confusion.held_updates:
                if held.state != "replaying":
                    continue
                events.append(
                    {
                        "event_type": "dialogue_insight",
                        "title": held.topic,
                        "metadata": {
                            "kind": "interest",
                            "confidence": held.value,
                            "evidence": "疑惑被确认为真实兴趣，重放此前搁置的兴趣变更。",
                            "source": "confusion_replay",
                            "occurrences": 1,
                        },
                    }
                )
        if not events:
            return {"replayed": 0, "confusions": 0}
        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=events,
            existing_preference=existing_preference,
        )
        # Freeze filter still applies (other topics may be frozen); the replayed
        # topics themselves are resolved, so they are no longer frozen.
        try:
            frozen_topics = self._confusion_manager.frozen_topics()
        except Exception:
            frozen_topics = set()
        if frozen_topics:
            updated_preference, held_updates = apply_confusion_freeze(
                before=existing_preference,
                after=updated_preference,
                frozen_topics=frozen_topics,
            )
            if held_updates:
                try:
                    self._confusion_manager.record_held_updates(held_updates)
                except Exception:
                    logger.debug("Failed to record held confusion updates", exc_info=True)
        with self._ledger.action(
            write_point="confusion_replay_preference",
            source="confusion",
            before=existing_preference,
            source_refs=[c.topic for c in pending if c.topic],
        ) as _entry:
            preference_layer.data.clear()
            preference_layer.data.update(updated_preference)
            preference_layer.save()
            _entry.after = dict(updated_preference)
        for confusion in pending:
            self._confusion_manager.mark_replay_applied(confusion.id)
        return {"replayed": len(events), "confusions": len(pending)}

    # -- Posture gate (Phase 3) ----------------------------------------------

    def _ledger_digest_for_gate(self) -> list[dict[str, object]]:
        """Compact 30-day ledger digest fed to the posture gate as context."""
        query = getattr(self._ledger_database, "query_profile_ledger", None)
        if not callable(query):
            return []
        try:
            rows = query(days=30, limit=30)
        except Exception:
            return []
        digest: list[dict[str, object]] = []
        for row in rows:
            digest.append(
                {
                    "write_point": str(row.get("write_point", "")),
                    "source": str(row.get("source", "")),
                    "outcome": str(row.get("outcome", "")),
                    "gate_verdict": str(row.get("gate_verdict", "")),
                }
            )
        return digest

    async def _gate_dialogue_candidates(
        self, candidates: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Access point ①: gate goal/value/state candidates (Phase 3).

        ``off`` returns the input untouched (byte-identical feed). Otherwise
        interest/dislike pass through; deep kinds are judged. Under enforce a
        rejected candidate is dropped and a downgraded one is demoted to an
        insight hypothesis (confidence × 0.6). Shadow keeps every candidate (its
        judgement is recorded asynchronously).
        """
        if not self._posture_gate.enabled:
            return candidates
        core_memory = self._memory.get_core_memory()
        ledger_digest = self._ledger_digest_for_gate()
        kept: list[dict[str, object]] = []
        downgraded: list[InsightHypothesis] = []
        for item in candidates:
            kind = str(item.get("kind", "")).strip()
            if kind not in _DEEP_CANDIDATE_KINDS:
                kept.append(item)
                continue
            content = str(item.get("content", ""))
            decision = await self._posture_gate.evaluate(
                write_point="dialogue_deep_candidate",
                change={
                    "kind": kind,
                    "content": content,
                    "confidence": item.get("confidence", 0.0),
                    "evidence": item.get("evidence", ""),
                },
                core_memory=core_memory,
                ledger_digest=ledger_digest,
                source_refs=[f"{kind}:{content[:60]}"],
            )
            if not decision.blocks:
                kept.append(item)
                continue
            # enforce reject / downgrade: excluded from the deep write.
            if decision.downgraded:
                downgraded.append(self._candidate_to_insight(item))
        if downgraded:
            self._persist_downgraded_insights(downgraded)
        return kept

    def _candidate_to_insight(self, item: dict[str, object]) -> InsightHypothesis:
        """Demote a downgraded deep candidate to a hypothesis (confidence × 0.6)."""
        raw_conf = item.get("confidence", 0.0)
        try:
            confidence = float(raw_conf)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            confidence = 0.0
        evidence_text = str(item.get("evidence", "")).strip()
        return InsightHypothesis(
            hypothesis=str(item.get("content", "")).strip(),
            evidence=[evidence_text] if evidence_text else [],
            confidence=round(max(0.0, min(1.0, confidence)) * 0.6, 4),
        )

    def _persist_confirmed_deep_candidates(
        self,
        candidates: list[dict[str, object]],
        *,
        turn_id: str | None = None,
    ) -> None:
        """Persist gate-accepted deep self-statements as validated hypotheses.

        First-person statements carry ``user_verdict="confirmed"`` — the user
        said it themselves, in their own words, and the posture gate already
        judged it consistent. Merged through ``merge_insights`` so a repeated
        statement reinforces one row instead of duplicating.
        """
        hypotheses: list[InsightHypothesis] = []
        for item in candidates:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            evidence_text = str(item.get("evidence", "")).strip()
            hypotheses.append(
                InsightHypothesis(
                    hypothesis=content,
                    evidence=[evidence_text] if evidence_text else [],
                    confidence=_clamp_init_draft_confidence(item.get("confidence", 0.8)),
                    validated=True,
                    created_at=datetime.now().date().isoformat(),
                    user_verdict="confirmed",
                )
            )
        if not hypotheses:
            return
        try:
            merged = self._insight_analyzer.merge_insights(self._load_insights(), hypotheses)
            self._save_insights(merged)
            self._ledger.record(
                write_point="dialogue_deep_selfstatement",
                source="chat",
                after={
                    "count": len(hypotheses),
                    "kinds": sorted({str(c.get("kind", "")) for c in candidates}),
                },
                source_refs=[h.hypothesis[:60] for h in hypotheses],
                turn_id=turn_id or "",
            )
        except Exception:
            logger.warning("deep self-statement persistence failed", exc_info=True)

    def _persist_downgraded_insights(self, insights: list[InsightHypothesis]) -> None:
        existing = self._load_insights()
        self._save_insights(existing + insights)
        for insight in insights:
            self._ledger.record(
                write_point="posture_gate_downgrade_insight",
                source="posture_gate",
                after={"hypothesis": insight.hypothesis[:80], "confidence": insight.confidence},
                source_refs=[insight.hypothesis[:60]],
                gate_verdict="downgrade",
            )

    async def _gate_soul_rebuild(
        self,
        *,
        trigger: str,
        existing_preference: dict[str, object],
        updated_preference: dict[str, object],
        source_refs: list[str],
        context: dict[str, object] | None = None,
    ) -> GateDecision:
        """Access point ③: gate a full soul rebuild (Phase 3, generalized r3/F4).

        Shared by all three rebuild triggers (dialogue / feedback_batch /
        confirmed_hypotheses). The judged snapshot carries the ``trigger``, its
        ledger ``write_point``, an old-soul interest-diff summary, and the
        trigger-specific ``context`` (dialogue candidates / feedback-batch
        summary / confirmed-hypothesis list) so the gate sees the right
        provenance. off never calls the gate (byte-identical) and returns an
        ``accept`` decision; shadow/accept → proceed; enforce downgrade/reject →
        abandon + ledger row.

        Returns the :class:`GateDecision`. Callers proceed on ``not
        decision.blocks``; the pending-rebuild state machine additionally reads
        ``decision.is_error`` to keep vs clear its marker (F7).
        """
        write_point = _REBUILD_WRITE_POINT.get(trigger, "soul_rebuild")
        if not self._posture_gate.enabled:
            return GateDecision(verdict=ACCEPT, enforced=False)
        core_memory = self._memory.get_core_memory()
        decision = await self._posture_gate.evaluate(
            write_point=write_point,
            change={
                "kind": "soul_rebuild",
                "trigger": trigger,
                "write_point": write_point,
                "before_interests": existing_preference.get("interests", []),
                "after_interests": updated_preference.get("interests", []),
                "context": context or {},
            },
            core_memory=core_memory,
            ledger_digest=self._ledger_digest_for_gate(),
            source_refs=source_refs,
        )
        if decision.blocks:
            self._ledger.record(
                write_point=write_point,
                source="posture_gate",
                before={"rebuild": "requested", "trigger": trigger},
                after={"rebuild": "abandoned", "verdict": decision.verdict},
                source_refs=source_refs,
                gate_verdict=decision.verdict,
                outcome="failed",
            )
        return decision

    @staticmethod
    def _candidate_ledger_refs(candidates: list[dict[str, object]]) -> list[str]:
        """Compact source refs for a dialogue-learning ledger row.

        Non-empty so the ledger's ``source_refs`` provenance is auditable even
        when candidate contents are terse.
        """
        refs = [
            f"{str(item.get('kind', '')).strip()}:{str(item.get('content', '')).strip()[:60]}"
            for item in candidates
            if str(item.get("content", "")).strip()
        ]
        return refs or ["dialogue"]

    def _candidate_ready_for_learning(self, candidate: dict[str, object]) -> bool:
        if bool(candidate.get("applied", False)):
            return False
        confidence = self._to_float(candidate.get("confidence", 0.0))
        occurrences = self._to_int(candidate.get("occurrences", 0))
        return confidence >= 0.8 or occurrences >= 2

    def _candidate_ready_for_immediate_dialogue_cognition(
        self,
        candidate: dict[str, object],
    ) -> bool:
        kind = str(candidate.get("kind", "")).strip()
        confidence = self._to_float(candidate.get("confidence", 0.0))
        if kind in {"goal", "dislike", "interest", "value"}:
            return confidence >= 0.8
        return confidence >= 0.9 and kind == "state"

    def _build_immediate_dialogue_cognition(
        self,
        candidate: dict[str, object],
    ) -> tuple[str, str, str, str, str, str]:
        kind = str(candidate.get("kind", "")).strip()
        content = str(candidate.get("content", "")).strip()
        evidence = str(candidate.get("evidence", "")).strip() or content
        context_line = self._build_dialogue_context_line(content)
        if not content:
            return "", "", "", "", "", ""
        if kind == "goal":
            return (
                f"阿B 刚记下了：你最近在意的是“{content}”。",
                "profile_shift",
                "画像里这类目标感会更靠前，后面更容易往因果链和结构解释上贴。",
                "因为你在聊天里主动提到这个目标，这是一次高置信即时信号。",
                evidence,
                context_line,
            )
        if kind == "dislike":
            return (
                f"阿B 刚听出来：像“{content}”这种你现在大概率不太想看。",
                "dislike_added",
                "画像里的避雷方向会更靠前，推荐时会更主动避开这类内容。",
                "因为你在聊天里明确表达了排斥，这比普通停留信号更直接。",
                evidence,
                context_line,
            )
        if kind == "interest":
            return (
                f"阿B 刚摸到一点：你最近可能开始吃“{content}”这一口。",
                "interest_added",
                "画像里这类兴趣会更靠前，后面更容易继续补同方向内容。",
                "因为你在聊天里主动提到这个方向，已经不只是被动刷到。",
                evidence,
                context_line,
            )
        if kind == "value":
            return (
                f"阿B 刚摸到一点：你其实挺看重“{content}”。",
                "profile_shift",
                "画像里的价值取向会更靠前，后面会更偏向同类表达方式。",
                "因为你在聊天里主动提到这类判断标准，这是一次高置信即时信号。",
                evidence,
                context_line,
            )
        return "", "", "", "", "", ""

    def _record_cognition_updates(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        previous_profile: dict[str, Any],
        current_profile: dict[str, Any],
        source: str,
    ) -> None:
        new_updates = self._build_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=previous_profile,
            current_profile=current_profile,
            source=source,
        )
        if not new_updates:
            return
        updates = self._memory.load_cognition_updates()
        updates.extend(new_updates)
        self._memory.save_cognition_updates(updates)

    def _build_cognition_updates(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        previous_profile: dict[str, Any],
        current_profile: dict[str, Any],
        source: str,
    ) -> list[dict[str, object]]:
        now = datetime.now().isoformat()
        updates: list[dict[str, object]] = []

        existing_interests = {
            self._normalize_text(str(item.get("name", ""))): item
            for item in self._as_dict_list(existing_preference.get("interests", []))
            if str(item.get("name", "")).strip()
        }
        for item in self._as_dict_list(updated_preference.get("interests", [])):
            name = str(item.get("name", "")).strip()
            normalized_name = self._normalize_text(name)
            if not normalized_name or normalized_name in existing_interests:
                continue
            weight = self._to_float(item.get("weight", 0.0))
            if weight < 0.75:
                continue
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "interest_added",
                    "summary": f"阿B 现在更确定你会吃“{name}”这一口。",
                    "context_line": self._build_topic_context_line([name]),
                    "impact": f"画像里“{name}”这条兴趣会更靠前，后面补货会更主动覆盖这个方向。",
                    "reasoning": "这不是一次偶发波动，更像是最近重复出现后的稳定兴趣强化。",
                    "evidence": f"最近聚合到的新主题里，“{name}”已经达到高权重。",
                    "confidence": round(weight, 4),
                    "created_at": now,
                    "source": source,
                    "source_label": self._build_source_label(source),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        existing_dislikes = {
            self._normalize_text(item)
            for item in self._as_str_list(existing_preference.get("disliked_topics", []))
        }
        for topic in self._as_str_list(updated_preference.get("disliked_topics", [])):
            normalized_topic = self._normalize_text(topic)
            if not normalized_topic or normalized_topic in existing_dislikes:
                continue
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "dislike_added",
                    "summary": f"阿B 记住了：像“{topic}”这种内容你大概率会划走。",
                    "context_line": self._build_topic_context_line([topic]),
                    "impact": f"画像里对“{topic}”这类内容的避雷会更明确。",
                    "reasoning": "这不是一次情绪化表达，而是最近反馈里重复浮出来的排斥方向。",
                    "evidence": f"最近聚合到的负反馈里，多次指向“{topic}”这个方向。",
                    "confidence": 0.86,
                    "created_at": now,
                    "source": source,
                    "source_label": self._build_source_label(source),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        if self._profile_shifted(previous_profile, current_profile):
            portrait = str(current_profile.get("personality_portrait", "")).strip()
            summary = portrait[:72].rstrip("，。！？,.!?") if portrait else "我对你又对上了一点。"
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "profile_shift",
                    "summary": summary,
                    "context_line": self._build_profile_shift_context_line(updated_preference),
                    "impact": "画像里的人格描述和关注重心已经发生可见调整。",
                    "reasoning": "这不是单次波动，而是最近重复出现后的稳定变化。",
                    "evidence": self._build_profile_shift_evidence(updated_preference),
                    "confidence": 0.9,
                    "created_at": now,
                    "source": "profile_refresh",
                    "source_label": self._build_source_label("profile_refresh"),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        return updates

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())

    def _build_profile_shift_evidence(self, preference: dict[str, Any]) -> str:
        interests = [
            str(item.get("name", "")).strip()
            for item in self._as_dict_list(preference.get("interests", []))
            if str(item.get("name", "")).strip()
        ][:2]
        if interests:
            return f"最近重复出现的主题包括：{' / '.join(interests)}。"
        return "最近重复出现的信号已经足够多，开始推动画像整体调整。"

    @staticmethod
    def _build_source_label(source: str) -> str:
        return SOURCE_LABELS.get(source.strip(), "")

    @staticmethod
    def _build_expand_hint(*, impact: str, reasoning: str, evidence: str) -> str:
        if any((impact.strip(), reasoning.strip(), evidence.strip())):
            return "expandable"
        return "summary_only"

    @staticmethod
    def _build_feedback_context_line(title: str) -> str:
        title_text = title.strip()
        if title_text:
            return f"来自：《{title_text}》"
        return "来自：这次推荐反馈"

    @staticmethod
    def _build_dialogue_context_line(content: str) -> str:
        if content.strip():
            return f"来自最近这轮聊天：{content.strip()}"
        return "来自最近这轮聊天"

    @staticmethod
    def _build_topic_context_line(topics: list[str]) -> str:
        normalized = [topic.strip() for topic in topics if topic.strip()]
        if normalized:
            return f"基于最近主题：{' / '.join(normalized[:3])}"
        return "基于最近几条相关内容"

    def _build_profile_shift_context_line(self, preference: dict[str, Any]) -> str:
        interests = [
            str(item.get("name", "")).strip()
            for item in self._as_dict_list(preference.get("interests", []))
            if str(item.get("name", "")).strip()
        ]
        dislikes = self._as_str_list(preference.get("disliked_topics", []))
        return self._build_topic_context_line([*interests[:2], *dislikes[:1]])

    @staticmethod
    def _is_retraction_feedback(event: dict[str, Any]) -> bool:
        """True when a deserialized feedback event is an X retraction."""
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            return False
        return str(metadata.get("feedback_type") or "").strip().lower() == "retraction"

    @staticmethod
    def _deserialize_event(event: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(event)
        for key in ("context", "metadata"):
            raw_value = normalized.get(key)
            if isinstance(raw_value, str):
                try:
                    parsed = json.loads(raw_value)
                except json.JSONDecodeError:
                    parsed = {}
                normalized[key] = parsed if isinstance(parsed, dict) else {}
        return normalized

    @staticmethod
    def _preference_changed_significantly(
        old_preference: dict[str, Any],
        new_preference: dict[str, Any],
    ) -> bool:
        def high_weight_interests(source: dict[str, Any]) -> dict[tuple[str, str], float]:
            items = source.get("interests", [])
            if not isinstance(items, list):
                return {}
            result: dict[tuple[str, str], float] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                weight = float(item.get("weight", 0.0) or 0.0)
                if weight < 0.6:
                    continue
                key = (str(item.get("name", "")).strip(), str(item.get("category", "")).strip())
                result[key] = weight
            return result

        old_interests = high_weight_interests(old_preference)
        new_interests = high_weight_interests(new_preference)
        if not old_interests and new_interests:
            return True
        changed_keys = set(old_interests) ^ set(new_interests)
        if len(changed_keys) >= 2:
            return True
        for key in set(old_interests) & set(new_interests):
            if abs(old_interests[key] - new_interests[key]) >= 0.2:
                return True
        old_disliked = {
            str(item).strip()
            for item in old_preference.get("disliked_topics", [])
            if str(item).strip()
        }
        new_disliked = {
            str(item).strip()
            for item in new_preference.get("disliked_topics", [])
            if str(item).strip()
        }
        return len(new_disliked - old_disliked) >= 1

    @staticmethod
    def _profile_shifted(previous_profile: dict[str, Any], current_profile: dict[str, Any]) -> bool:
        if not current_profile:
            return False
        if not previous_profile:
            return bool(
                SoulEngine._as_str_list(current_profile.get("core_traits", []))
                or SoulEngine._as_str_list(current_profile.get("deep_needs", []))
                or str(current_profile.get("personality_portrait", "")).strip()
            )
        previous_traits = set(SoulEngine._as_str_list(previous_profile.get("core_traits", [])))
        current_traits = set(SoulEngine._as_str_list(current_profile.get("core_traits", [])))
        if current_traits - previous_traits:
            return True
        previous_needs = set(SoulEngine._as_str_list(previous_profile.get("deep_needs", [])))
        current_needs = set(SoulEngine._as_str_list(current_profile.get("deep_needs", [])))
        if current_needs - previous_needs:
            return True
        previous_portrait = SoulEngine._normalize_text(
            str(previous_profile.get("personality_portrait", ""))
        )
        current_portrait = SoulEngine._normalize_text(
            str(current_profile.get("personality_portrait", ""))
        )
        return bool(
            previous_portrait and current_portrait and previous_portrait != current_portrait
        )

    @staticmethod
    def _as_dict_list(raw_value: object) -> list[dict[str, Any]]:
        if not isinstance(raw_value, list):
            return []
        return [item for item in raw_value if isinstance(item, dict)]

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

    @staticmethod
    def _to_int(raw_value: object) -> int:
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
        if isinstance(raw_value, str):
            try:
                return int(raw_value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _to_float(raw_value: object) -> float:
        if isinstance(raw_value, bool):
            return float(raw_value)
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                return 0.0
        return 0.0
