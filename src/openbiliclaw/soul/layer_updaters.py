"""Per-layer update functions for the ProfileUpdatePipeline.

Each layer has its own update logic: Surface uses computation, Interest
delegates to PreferenceAnalyzer, Role/Values/Core use LLM with diff protection.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING, Any

from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    JSONValue,
    parse_llm_json_tolerant,
)
from openbiliclaw.soul.dislike_writeback import purge_pool_for_new_dislikes

if TYPE_CHECKING:
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
    from openbiliclaw.soul.profile import OnionProfile
    from openbiliclaw.soul.profile_builder import ProfileBuilder

from .pipeline import LayerUpdateResult, OnionLayer, SignalType

logger = logging.getLogger(__name__)

# Unified interest line (spec 2026-07-27): a drained batch carrying at least one
# FEEDBACK signal inherits the retired feedback batch's privileges — dislike
# archive, gated soul rebuild, held replay — and its ledger provenance.
_FEEDBACK_LEDGER_SOURCE = "feedback"


def _feedback_signal_ids(signals: list[dict[str, object]]) -> list[str]:
    """Signal ids of the FEEDBACK rows in this batch (empty ⇒ not a feedback batch)."""
    return [
        str(sig.get("id", "")).strip()
        for sig in signals
        if sig.get("signal_type") == SignalType.FEEDBACK.value
    ]


LayerUpdater = Callable[..., Awaitable[LayerUpdateResult]]


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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _string_set(value: object) -> set[str]:
    return set(_string_list(value))


def _json_list(value: object) -> list[JSONValue]:
    return value if isinstance(value, list) else []


def _json_object(value: object) -> dict[str, JSONValue]:
    return value if isinstance(value, dict) else {}


# Deep layers are SEALED from event-driven pipeline writes (deep-line
# consolidation — P1 retired). VALUES/CORE change only via a gated soul rebuild
# (access point ③); a direct ``update_layer`` call for either is a defensive
# no-op + WARNING, so no future wiring can re-open the bypass (invariant 1, F3).
_SEALED_DEEP_LAYERS = frozenset({OnionLayer.VALUES, OnionLayer.CORE})


async def update_layer(
    *,
    layer: OnionLayer,
    signals: list[dict[str, object]],
    profile: OnionProfile,
    memory: MemoryManager,
    preference_analyzer: PreferenceAnalyzer,
    profile_builder: ProfileBuilder,
    embedding_service: Any | None = None,
    llm_service: Any | None = None,
    posture_gate: Any | None = None,
    feedback_hooks: Any | None = None,
) -> LayerUpdateResult:
    """Dispatch to the appropriate layer updater.

    VALUES/CORE are sealed: a direct call for either is a defensive no-op with a
    WARNING — the event-driven pipeline no longer writes deep layers (access
    point ② retired); they change only through a gated soul rebuild. The
    ``posture_gate`` parameter is accepted for signature stability but is no
    longer consulted here.
    """
    if layer in _SEALED_DEEP_LAYERS:
        logger.warning(
            "update_layer called for sealed deep layer %s; ignoring "
            "(P1 retired — deep change flows only through a gated soul rebuild)",
            getattr(layer, "value", layer),
        )
        return LayerUpdateResult(layer=layer, changed=False)

    updater: LayerUpdater | None = _LAYER_UPDATERS.get(layer)
    if updater is None:
        return LayerUpdateResult(layer=layer, changed=False)

    result = await updater(
        signals=signals,
        profile=profile,
        memory=memory,
        preference_analyzer=preference_analyzer,
        profile_builder=profile_builder,
        embedding_service=embedding_service,
        llm_service=llm_service,
        feedback_hooks=feedback_hooks,
    )

    # Ledger write point D5 #3: pipeline per-layer updater persistence. One row
    # per layer that actually changed (a no-op update writes nothing).
    if result.changed:
        # Ledger continuity (spec invariant 5): a feedback-triggered consumption
        # keeps the ``feedback`` provenance the retired ``feedback_preference_
        # overwrite`` row carried, so `openbiliclaw ledger` can still follow the
        # feedback line across the merge.
        source = (
            _FEEDBACK_LEDGER_SOURCE
            if _feedback_signal_ids(signals)
            else f"pipeline:{getattr(layer, 'value', str(layer))}"
        )
        _record_layer_ledger(memory=memory, layer=layer, result=result, source=source)
    return result


def _record_layer_ledger(
    *,
    memory: MemoryManager,
    layer: OnionLayer,
    result: LayerUpdateResult,
    source: str = "",
) -> None:
    from openbiliclaw.soul.ledger import ProfileLedger

    ledger = ProfileLedger(getattr(memory, "_database", None))
    layer_name = getattr(layer, "value", str(layer))
    ledger.record(
        write_point="pipeline_layer_update",
        source=source or f"pipeline:{layer_name}",
        before={"layer": layer_name},
        after={"layer": layer_name, "changes": list(result.changes)},
        source_refs=list(result.changes) or [f"layer:{layer_name}"],
        outcome="success",
    )


def _apply_topic_lifecycle_evidence(
    *,
    memory: MemoryManager,
    existing_preference: dict[str, Any],
    updated_preference: dict[str, Any],
) -> None:
    """Stamp lifecycle state on interests produced by the incremental pipeline.

    Without this, only ``analyze_events`` / ``learn_from_dialogue`` /
    the feedback batch stamped ``state``. Ordinary browsing — the dominant
    input path — went through here and produced interests with no ``state``
    at all, which ``get_state()`` reads as ``active``. New topics therefore
    skipped the trial period entirely and immediately competed for
    recommendation weight, and the 12h ``scan_lifecycle`` never backfilled
    them (it treats a stateless interest as already active).

    Only topics whose ``last_seen`` changed count as evidence; a retained item
    in the analyzer's full merged snapshot merely keeps its lifecycle fields.
    Mirrors ``SoulEngine._apply_topic_lifecycle_evidence``; ``source`` is
    ``pipeline`` so the ledger still shows which path stamped the transition.
    Best-effort: any failure is logged at DEBUG and never breaks the update.
    """
    try:
        from openbiliclaw.soul.ledger import ProfileLedger
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
        if not transitions:
            return
        ledger = ProfileLedger(getattr(memory, "_database", None))
        for tr in transitions:
            ledger.record(
                write_point="topic_lifecycle",
                source="pipeline",
                before={"topic": tr.name, "state": tr.from_state},
                after={"topic": tr.name, "state": tr.to_state},
                source_refs=[f"reason:{tr.reason}"],
            )
    except Exception:
        logger.debug("pipeline topic lifecycle evidence overlay failed", exc_info=True)


# ---------------------------------------------------------------------------
# Surface layer — computational, no LLM
# ---------------------------------------------------------------------------


async def _update_surface(
    *,
    signals: list[dict[str, object]],
    profile: OnionProfile,
    **_: Any,
) -> LayerUpdateResult:
    """Update surface layer from behavioral signals using pure computation."""
    changes: list[str] = []

    # Count event types for style inference
    view_count = 0
    search_count = 0
    for sig in signals:
        payload = sig.get("payload", {})
        if isinstance(payload, dict):
            event_type = str(payload.get("event_type", ""))
            if event_type == "view":
                view_count += 1
            elif event_type == "search":
                search_count += 1

    # If we have enough behavioral data, adjust depth preference
    if view_count >= 2:
        old_depth = profile.surface.style.depth_preference
        # More search events relative to views suggests deeper engagement
        depth_signal = min(1.0, 0.5 + (search_count / max(view_count, 1)) * 0.3)
        new_depth = round(old_depth * 0.7 + depth_signal * 0.3, 2)
        if abs(new_depth - old_depth) > 0.05:
            profile.surface.style.depth_preference = new_depth
            changes.append(f"depth_preference: {old_depth:.2f} → {new_depth:.2f}")

    return LayerUpdateResult(
        layer=OnionLayer.SURFACE,
        changed=bool(changes),
        changes=changes,
        signals_consumed=len(signals),
        trigger="行为模式分析",
        evidence=f"{view_count} views, {search_count} searches",
        timestamp=datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# Interest layer — LLM + tree merge
# ---------------------------------------------------------------------------


async def _update_interest(
    *,
    signals: list[dict[str, object]],
    profile: OnionProfile,
    memory: MemoryManager,
    preference_analyzer: PreferenceAnalyzer,
    embedding_service: Any | None = None,
    llm_service: Any | None = None,
    feedback_hooks: Any | None = None,
    **_: Any,
) -> LayerUpdateResult:
    """Update interest layer by delegating to PreferenceAnalyzer.

    When the drained batch carries FEEDBACK signals and the unified interest
    line is on (``feedback_hooks`` wired), this is also the retired feedback
    batch's consumption point: newly disliked topics are archived before the
    preference write, and ``feedback_context`` asks the pipeline to run the
    gated soul rebuild + held replay after the profile write.
    """
    # Convert signals back to event format for PreferenceAnalyzer
    events: list[dict[str, Any]] = []
    for sig in signals:
        payload = sig.get("payload", {})
        if isinstance(payload, dict):
            events.append(dict(payload))

    if not events:
        return LayerUpdateResult(layer=OnionLayer.INTEREST, changed=False)

    preference_layer = memory.get_layer("preference")
    existing_preference = dict(preference_layer.data)

    pre_update_profile = deepcopy(profile)

    # A small event batch is ambiguous on its own — three woodworking videos
    # could be a new interest or an old one resurfacing. Passing the recent
    # cognition tail (same window regenerate_portrait uses) lets the analyzer
    # interpret the batch against what the system already believes instead of
    # in a vacuum. Init chunks and the feedback batch deliberately do NOT pass
    # this: init has no cognition yet, and feedback is judged on its own words.
    from .profile import awareness_note_to_dict, insight_hypothesis_to_dict

    awareness_notes = [awareness_note_to_dict(n) for n in profile.recent_awareness[-5:]]
    active_insights = [insight_hypothesis_to_dict(i) for i in profile.active_insights[-5:]]

    try:
        updated_preference = await preference_analyzer.analyze_events(
            events=events,
            existing_preference=existing_preference,
            awareness_notes=awareness_notes or None,
            active_insights=active_insights or None,
        )
    except Exception:
        logger.exception("PreferenceAnalyzer failed during interest update")
        return LayerUpdateResult(layer=OnionLayer.INTEREST, changed=False)

    # Count this analysis as one unit of lifecycle evidence before persisting,
    # so pipeline-born topics enter `trial` like every other write path.
    _apply_topic_lifecycle_evidence(
        memory=memory,
        existing_preference=existing_preference,
        updated_preference=updated_preference,
    )

    old_dislikes = _string_set(existing_preference.get("disliked_topics"))
    new_dislikes = _string_set(updated_preference.get("disliked_topics"))
    newly_added_dislikes = new_dislikes - old_dislikes

    # Feedback-batch privilege ①: archive (never delete) interests the user just
    # told us to avoid, in the same snapshot that gets persisted and populated
    # into the onion profile below.
    feedback_signal_ids = _feedback_signal_ids(signals)
    if feedback_signal_ids and newly_added_dislikes and feedback_hooks is not None:
        try:
            feedback_hooks.archive_dislikes(updated_preference, sorted(newly_added_dislikes))
        except Exception:
            logger.debug("feedback dislike archive hook failed", exc_info=True)

    # Persist flat preference (unchanged pipeline)
    preference_layer.data.clear()
    preference_layer.data.update(updated_preference)
    preference_layer.save()

    # Update the onion interest + surface layers from flat preference
    profile.populate_from_flat_preference(updated_preference)

    # Sync cognitive_style (not modeled in PreferenceLayer, bypasses populate)
    cs = updated_preference.get("cognitive_style")
    if isinstance(cs, list):
        profile.surface.cognitive_style = [str(s) for s in cs if s]

    # Detect changes
    changes: list[str] = []
    old_interests = {
        str(item.get("name", "")).strip(): _coerce_float(item.get("weight", 0))
        for item in _json_list(existing_preference.get("interests"))
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }
    new_interests = {
        str(item.get("name", "")).strip(): _coerce_float(item.get("weight", 0))
        for item in _json_list(updated_preference.get("interests"))
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }
    for name in new_interests:
        if name not in old_interests:
            changes.append(f"新增兴趣: {name} ({new_interests[name]:.2f})")
        elif abs(new_interests[name] - old_interests.get(name, 0)) > 0.15:
            changes.append(
                f"兴趣权重变化: {name} {old_interests[name]:.2f} → {new_interests[name]:.2f}"
            )

    for topic in newly_added_dislikes:
        changes.append(f"新增讨厌: {topic}")

    if newly_added_dislikes:
        database = getattr(memory, "_database", None)
        changes.extend(
            await purge_pool_for_new_dislikes(
                database=database,
                embedding_service=embedding_service,
                llm_service=llm_service,
                newly_added=sorted(newly_added_dislikes),
                all_dislikes=sorted(new_dislikes),
            )
        )

    # Feed speculative_interests to speculator as seed candidates
    speculative_seeds = updated_preference.get("speculative_interests")
    if isinstance(speculative_seeds, list) and speculative_seeds:
        try:
            from openbiliclaw.soul.speculator import InterestSpeculator

            data_dir = getattr(memory, "_data_dir", None)
            if data_dir:
                speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)
                seed_coverage_profile = deepcopy(pre_update_profile)
                seed_coverage_profile.interest.likes.extend(profile.interest.likes)
                runtime_state: dict[str, object] = {}
                load_runtime_state = getattr(memory, "load_discovery_runtime_state", None)
                if callable(load_runtime_state):
                    runtime_state = load_runtime_state()
                probed_domains_raw = runtime_state.get("probed_domains", {})
                probed_domains = (
                    set(probed_domains_raw) if isinstance(probed_domains_raw, dict) else set()
                )
                added = speculator.ingest_seeds(
                    speculative_seeds,
                    profile=seed_coverage_profile,
                    probed_domains=probed_domains,
                    feedback_history=runtime_state.get("probe_feedback_history", []),
                )
                if added:
                    changes.append(f"注入 {added} 条猜测兴趣种子")
        except Exception:
            logger.debug("Speculator seed ingestion skipped", exc_info=True)

    # Feedback-batch privileges ②③ run after the profile write — hand the
    # pipeline everything the gated rebuild + held replay need.
    feedback_context: dict[str, object] | None = None
    if feedback_signal_ids and feedback_hooks is not None:
        feedback_context = {
            "existing_preference": existing_preference,
            "updated_preference": updated_preference,
            "feedback_count": len(feedback_signal_ids),
            "source_refs": [f"feedback_signal:{sid}" for sid in feedback_signal_ids if sid]
            or ["feedback_batch"],
        }

    return LayerUpdateResult(
        layer=OnionLayer.INTEREST,
        changed=bool(changes),
        changes=changes,
        signals_consumed=len(signals),
        trigger="偏好分析",
        evidence=f"分析了 {len(events)} 条事件",
        timestamp=datetime.now().isoformat(),
        feedback_context=feedback_context,
    )


# ---------------------------------------------------------------------------
# Role layer — LLM with diff protection
# ---------------------------------------------------------------------------


async def _update_role(
    *,
    signals: list[dict[str, object]],
    profile: OnionProfile,
    memory: MemoryManager,
    profile_builder: ProfileBuilder,
    **_: Any,
) -> LayerUpdateResult:
    """Update role layer (life_stage, current_phase) from accumulated signals.

    Uses LLM with diff-protection: only apply if LLM explicitly proposes change.
    """
    # Collect evidence from signals
    evidence_parts: list[str] = []
    for sig in signals:
        payload = sig.get("payload", {})
        if isinstance(payload, dict):
            title = str(payload.get("title", ""))
            event_type = str(payload.get("event_type", ""))
            up_name = str(payload.get("up_name", ""))
            content = str(payload.get("content", ""))
            if title:
                up_suffix = f" (UP:{up_name})" if up_name else ""
                evidence_parts.append(f"[{event_type}] {title}{up_suffix}")
            elif content:
                evidence_parts.append(content)

    if not evidence_parts:
        return LayerUpdateResult(layer=OnionLayer.ROLE, changed=False)

    from openbiliclaw.llm.prompts import build_role_delta_prompt

    messages = build_role_delta_prompt(
        current_life_stage=profile.role.life_stage,
        current_phase=profile.role.current_phase,
        evidence=evidence_parts,
    )

    try:
        # Intentionally carries core memory: this updates the profile's own role
        # layer, and the injected "who the user is" context (portrait / current
        # traits / awareness) helps the LLM connect new evidence to the user's life
        # situation before proposing a delta. Kept per the Task 8 audit.
        response = await profile_builder.registry.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            caller="soul.role_update",
        )
        result = parse_llm_json_tolerant(response.content)
        if not isinstance(result, dict):
            raise ValueError("role delta response must be a JSON object")
    except Exception:
        logger.exception("Role delta LLM call failed")
        return LayerUpdateResult(
            layer=OnionLayer.ROLE,
            changed=False,
            signals_consumed=len(signals),
            timestamp=datetime.now().isoformat(),
        )

    changes: list[str] = []
    if result.get("changed"):
        new_stage = str(result.get("life_stage", "")).strip()
        new_phase = str(result.get("current_phase", "")).strip()
        reason = str(result.get("reason", ""))

        if new_stage and new_stage != profile.role.life_stage:
            changes.append(f"life_stage: {profile.role.life_stage!r} → {new_stage!r}")
            profile.role.life_stage = new_stage
        if new_phase and new_phase != profile.role.current_phase:
            changes.append(f"current_phase: {profile.role.current_phase!r} → {new_phase!r}")
            profile.role.current_phase = new_phase
        if changes:
            logger.info("Role updated: %s (reason: %s)", "; ".join(changes), reason[:60])

    return LayerUpdateResult(
        layer=OnionLayer.ROLE,
        changed=bool(changes),
        changes=changes,
        signals_consumed=len(signals),
        trigger="角色信号分析",
        evidence="; ".join(evidence_parts[:3]),
        timestamp=datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# Values layer — LLM with delta-only updates
# ---------------------------------------------------------------------------


async def _update_values(
    *,
    signals: list[dict[str, object]],
    profile: OnionProfile,
    profile_builder: ProfileBuilder,
    **_: Any,
) -> LayerUpdateResult:
    """Update values layer using LLM delta (add/remove, max 1 per cycle)."""
    evidence_parts: list[str] = []
    for sig in signals:
        payload = sig.get("payload", {})
        if isinstance(payload, dict):
            title = str(payload.get("title", ""))
            event_type = str(payload.get("event_type", ""))
            content = str(payload.get("content", ""))
            if title:
                evidence_parts.append(f"[{event_type}] {title}")
            elif content:
                evidence_parts.append(content)

    if not evidence_parts:
        return LayerUpdateResult(layer=OnionLayer.VALUES, changed=False)

    # Inject profile context so LLM can connect evidence to user's life situation
    ctx_parts: list[str] = []
    if profile.role.life_stage:
        ctx_parts.append(f"生活阶段: {profile.role.life_stage}")
    if profile.role.current_phase:
        ctx_parts.append(f"当前状态: {profile.role.current_phase}")
    interest_names = (
        [d.domain for d in profile.interest.likes[:6]] if profile.interest.likes else []
    )
    if interest_names:
        ctx_parts.append(f"主要兴趣: {', '.join(interest_names)}")
    if ctx_parts:
        evidence_parts.insert(0, "【用户背景】" + "; ".join(ctx_parts))

    from openbiliclaw.llm.prompts import build_values_delta_prompt

    messages = build_values_delta_prompt(
        current_values=profile.values_layer.values,
        current_drivers=profile.values_layer.motivational_drivers,
        evidence=evidence_parts,
    )

    try:
        # Intentionally carries core memory: values-layer deltas need the user's
        # core context ("connect evidence to user's life situation", see the
        # curated 用户背景 above) — portrait / current values / awareness legitimately
        # inform whether a value should be added or removed. Kept per Task 8 audit.
        response = await profile_builder.registry.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            caller="soul.values_update",
        )
        result = parse_llm_json_tolerant(response.content)
        if not isinstance(result, dict):
            raise ValueError("values delta response must be a JSON object")
    except Exception:
        logger.exception("Values delta LLM call failed")
        return LayerUpdateResult(
            layer=OnionLayer.VALUES,
            changed=False,
            signals_consumed=len(signals),
            timestamp=datetime.now().isoformat(),
        )

    changes: list[str] = []
    if result.get("changed"):
        new_values = result.get("values")
        new_drivers = result.get("motivational_drivers")
        reason = str(result.get("reason", ""))

        new_values_list = _string_list(new_values)
        if new_values_list and set(new_values_list) != set(profile.values_layer.values):
            old = profile.values_layer.values
            profile.values_layer.values = new_values_list
            added = set(new_values_list) - set(old)
            removed = set(old) - set(new_values_list)
            if added:
                changes.append(f"新增价值观: {', '.join(added)}")
            if removed:
                changes.append(f"移除价值观: {', '.join(removed)}")

        new_drivers_list = _string_list(new_drivers)
        if new_drivers_list and set(new_drivers_list) != set(
            profile.values_layer.motivational_drivers
        ):
            old = profile.values_layer.motivational_drivers
            profile.values_layer.motivational_drivers = new_drivers_list
            added = set(new_drivers_list) - set(old)
            removed = set(old) - set(new_drivers_list)
            if added:
                changes.append(f"新增驱动: {', '.join(added)}")
            if removed:
                changes.append(f"移除驱动: {', '.join(removed)}")

        if changes:
            logger.info("Values updated: %s (reason: %s)", "; ".join(changes), reason[:60])

    return LayerUpdateResult(
        layer=OnionLayer.VALUES,
        changed=bool(changes),
        changes=changes,
        signals_consumed=len(signals),
        trigger="价值观信号分析",
        evidence="; ".join(evidence_parts[:3]),
        timestamp=datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# Core layer — LLM with strongest diff protection
# ---------------------------------------------------------------------------


async def _update_core(
    *,
    signals: list[dict[str, object]],
    profile: OnionProfile,
    profile_builder: ProfileBuilder,
    **_: Any,
) -> LayerUpdateResult:
    """Update core layer (traits, needs, MBTI) with strong diff protection."""
    evidence_parts: list[str] = []
    for sig in signals:
        payload = sig.get("payload", {})
        if isinstance(payload, dict):
            title = str(payload.get("title", ""))
            event_type = str(payload.get("event_type", ""))
            content = str(payload.get("content", ""))
            if title:
                evidence_parts.append(f"[{event_type}] {title}")
            elif content:
                evidence_parts.append(content)

    if not evidence_parts:
        return LayerUpdateResult(layer=OnionLayer.CORE, changed=False)

    from openbiliclaw.llm.prompts import build_core_delta_prompt

    mbti_dict: dict[str, object] = {}
    if profile.core.mbti.type:
        mbti_dict = {
            "type": profile.core.mbti.type,
            "confidence": profile.core.mbti.confidence,
            "dimensions": {
                k: {"pole": v.pole, "strength": v.strength}
                for k, v in profile.core.mbti.dimensions.items()
            },
        }

    messages = build_core_delta_prompt(
        current_traits=profile.core.core_traits,
        current_needs=profile.core.deep_needs,
        current_mbti=mbti_dict,
        evidence=evidence_parts,
    )

    try:
        # Intentionally carries core memory: this updates the deepest core layer
        # (traits / needs / MBTI) under the strongest diff protection, and the
        # injected core context helps the LLM weigh whether new evidence truly
        # justifies a core change. Kept per the Task 8 audit.
        response = await profile_builder.registry.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            caller="soul.core_update",
        )
        result = parse_llm_json_tolerant(response.content)
        if not isinstance(result, dict):
            raise ValueError("core delta response must be a JSON object")
    except Exception:
        logger.exception("Core delta LLM call failed")
        return LayerUpdateResult(
            layer=OnionLayer.CORE,
            changed=False,
            signals_consumed=len(signals),
            timestamp=datetime.now().isoformat(),
        )

    changes: list[str] = []
    if result.get("changed"):
        reason = str(result.get("reason", ""))

        new_traits = result.get("core_traits")
        new_traits_list = _string_list(new_traits)
        if new_traits_list and set(new_traits_list) != set(profile.core.core_traits):
            added = set(new_traits_list) - set(profile.core.core_traits)
            removed = set(profile.core.core_traits) - set(new_traits_list)
            profile.core.core_traits = new_traits_list
            if added:
                changes.append(f"新增特质: {', '.join(added)}")
            if removed:
                changes.append(f"移除特质: {', '.join(removed)}")

        new_needs = result.get("deep_needs")
        new_needs_list = _string_list(new_needs)
        if new_needs_list and set(new_needs_list) != set(profile.core.deep_needs):
            added = set(new_needs_list) - set(profile.core.deep_needs)
            removed = set(profile.core.deep_needs) - set(new_needs_list)
            profile.core.deep_needs = new_needs_list
            if added:
                changes.append(f"新增需求: {', '.join(added)}")
            if removed:
                changes.append(f"移除需求: {', '.join(removed)}")

        # MBTI update (very rare)
        new_mbti = result.get("mbti")
        if isinstance(new_mbti, dict) and new_mbti.get("type"):
            from openbiliclaw.soul.profile import MBTI, MBTIDimension

            new_type = str(new_mbti["type"]).upper()
            if new_type != profile.core.mbti.type:
                changes.append(f"MBTI: {profile.core.mbti.type} → {new_type}")
                dims = {}
                for k, v in _json_object(new_mbti.get("dimensions")).items():
                    if isinstance(v, dict):
                        dims[k] = MBTIDimension(
                            pole=str(v.get("pole", "")),
                            strength=_coerce_float(v.get("strength", 0.5), default=0.5),
                        )
                profile.core.mbti = MBTI(
                    type=new_type,
                    confidence=_coerce_float(new_mbti.get("confidence", 0.6), default=0.6),
                    dimensions=dims,
                )

        if changes:
            logger.info("Core updated: %s (reason: %s)", "; ".join(changes), reason[:60])

    return LayerUpdateResult(
        layer=OnionLayer.CORE,
        changed=bool(changes),
        changes=changes,
        signals_consumed=len(signals),
        trigger="核心信号分析",
        evidence="; ".join(evidence_parts[:3]),
        timestamp=datetime.now().isoformat(),
    )


# ---------------------------------------------------------------------------
# Portrait regeneration
# ---------------------------------------------------------------------------


async def regenerate_portrait(
    *,
    profile: OnionProfile,
    profile_builder: ProfileBuilder,
    memory: MemoryManager,
) -> str:
    """Regenerate personality_portrait from current profile state.

    Only called when Core or Values layer actually changes.
    Returns the new portrait text, or empty string on failure.
    """
    from .profile import awareness_note_to_dict, insight_hypothesis_to_dict

    try:
        legacy_profile = await profile_builder.build(
            history=[],
            preference=memory.get_layer("preference").data,
            # Both windows are chronological oldest→newest; take the tail.
            awareness_notes=[awareness_note_to_dict(n) for n in profile.recent_awareness[-5:]],
            active_insights=[insight_hypothesis_to_dict(i) for i in profile.active_insights[-5:]],
        )
        return legacy_profile.personality_portrait
    except Exception:
        logger.exception("Failed to regenerate portrait")
        return ""


# ---------------------------------------------------------------------------
# Updater dispatch table
# ---------------------------------------------------------------------------

# VALUES/CORE are intentionally absent (deep-line consolidation — P1 retired):
# ``update_layer`` seals them before dispatch. ``_update_values`` / ``_update_core``
# remain defined (delta-prompt logic retained for direct tooling / potential
# rebuild-input reuse) but are no longer wired into the event-driven pipeline.
_LAYER_UPDATERS: dict[OnionLayer, LayerUpdater] = {
    OnionLayer.SURFACE: _update_surface,
    OnionLayer.INTEREST: _update_interest,
    OnionLayer.ROLE: _update_role,
}
