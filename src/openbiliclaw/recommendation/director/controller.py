"""A single pure proposal seam for three recommendation-director strategies."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from .composer import PairComposer
from .horizon_models import (
    DirectorPair,
    DirectorProposal,
    DirectorResult,
    DirectorStrategy,
    HorizonPlan,
    freeze_request,
)
from .models import CompositionRequest, ScreenProposal, digest
from .policy import DIRECTOR_POLICY_VERSION, build_screen_brief, review_screen
from .proposer import largest_feasible_count
from .repair import DraftRepairer

if TYPE_CHECKING:
    from openbiliclaw.decision.base import DecisionProvider
    from openbiliclaw.decision.context_budget import JevContextBudget


def plan_needs_replanning(
    request: CompositionRequest, horizon: HorizonPlan, *, strategy: DirectorStrategy
) -> bool:
    """Whether the slow path must replace this horizon before its next execution.

    A new explicit direction invalidates both Agent modes. Hybrid can audit
    smaller feedback changes locally. Pure Agent preserves a plan for a fully
    explained window of exposure/weak-click events, not for missing evidence.
    """
    if horizon.planning_policy_version != DIRECTOR_POLICY_VERSION:
        return True
    if horizon.planning_explicit_intent != request.session.explicit_intent:
        return True
    if horizon.planning_profile_digest != request.session.profile_digest:
        return True
    if strategy != "agent_only":
        return False
    watermark = request.session.latest_preference_revision
    if watermark is not None:
        # SQLite computes this across all stored feedback in the same snapshot;
        # a burst of ACKs/clicks cannot hide a prior explicit preference change.
        return (
            request.session.state_revision < horizon.planning_state_revision
            or watermark > horizon.planning_state_revision
        )
    if (
        horizon.planning_state_revision == request.session.state_revision
        and horizon.planning_feedback_cursor == request.session.feedback_cursor
    ):
        return False
    new_events = tuple(
        event
        for event in request.session.real_feedback
        if event.event_ref not in horizon.planning_feedback_event_refs
    )
    # One non-noop receipt advances the state once, even when it contains many
    # card events. Cursors may jump or stay unchanged for late-ingested events;
    # their numeric distance does not count receipts. Truncated history cannot
    # establish that all intervening changes were merely weak clicks/ACKs.
    revision_delta = request.session.state_revision - horizon.planning_state_revision
    return (
        revision_delta != len(new_events)
        or not new_events
        or any(event.kind not in {"presented", "click"} for event in new_events)
    )


class DirectorController:
    """Return proposals and CAS patches; never save or expose recommendations.

    Horizon planning is separately scheduled by the caller. This fast path never
    starts a hidden Agent call; Jev recurrence never reads an Agent plan, and
    pure-Agent orchestration never invokes the optional decision provider.
    """

    def __init__(
        self,
        provider: DecisionProvider | None = None,
        *,
        max_patch_pairs: int = 1,
        context_budget: JevContextBudget | None = None,
    ) -> None:
        self._provider = provider
        self._max_patch_pairs = max_patch_pairs
        self._context_budget = context_budget
        # Validate once, including when a pure-Jev caller supplies the budget.
        DraftRepairer(max_patch_pairs=max_patch_pairs)

    async def prepare_next(
        self,
        request: CompositionRequest,
        *,
        strategy: DirectorStrategy,
        horizon: HorizonPlan | None = None,
        screen_index: int = 0,
        now_epoch: float | None = None,
    ) -> DirectorResult:
        started = time.monotonic()
        frozen = freeze_request(request)
        if strategy not in {"jev_recurrent", "agent_jev", "agent_only"}:
            raise ValueError("invalid director strategy")
        if type(screen_index) is not int or screen_index < 0:
            raise ValueError("invalid future screen index")
        now = time.time() if now_epoch is None else now_epoch
        if not math.isfinite(now) or now < 0:
            raise ValueError("invalid current time")
        if strategy == "jev_recurrent":
            composed = await PairComposer(
                self._provider, context_budget=self._context_budget
            ).prepare_next(frozen)
            return DirectorResult(proposal=self._from_composer(frozen, composed, strategy))
        reason = "agent_plan_missing"
        if horizon is not None:
            try:
                horizon = HorizonPlan.model_validate(
                    horizon.model_dump(mode="python", warnings="error")
                )
            except (ValueError, TypeError, OverflowError, AttributeError):
                horizon = None
                reason = "agent_plan_invalid"
        if horizon is not None:
            if (
                horizon.session_ref != frozen.session.session_ref
                or horizon.stream_ref != frozen.session.stream_ref
                or horizon.configuration_revision != frozen.session.configuration_revision
                or horizon.feature_schema_version != frozen.feature_schema_version
            ):
                horizon = None
                reason = "agent_plan_binding_mismatch"
            elif now >= horizon.expires_at or now < horizon.created_at:
                horizon = None
                reason = "agent_plan_expired"
            elif screen_index >= len(horizon.screens):
                horizon = None
                reason = "agent_plan_exhausted"
            elif horizon.screens[screen_index].target_count != frozen.target_count:
                horizon = None
                reason = "agent_plan_screen_size_changed"
            elif plan_needs_replanning(frozen, horizon, strategy=strategy):
                # Without the fast semantic auditor, do not silently execute a
                # plan under changed feedback/intent. The caller schedules a new
                # Agent plan; this response is a clearly attributed baseline.
                horizon = None
                reason = "agent_plan_state_changed"
        if horizon is None:
            provider = self._provider if strategy == "agent_jev" else None
            fallback = await PairComposer(
                provider, context_budget=self._context_budget
            ).prepare_next(frozen)
            return DirectorResult(
                proposal=self._from_composer(frozen, fallback, strategy, reason),
                replan_required=True,
            )
        repair = await DraftRepairer(
            self._provider if strategy == "agent_jev" else None,
            max_patch_pairs=self._max_patch_pairs,
            context_budget=self._context_budget,
        ).repair(frozen, horizon)
        if now + (time.monotonic() - started) >= horizon.expires_at:
            # Recheck TTL after awaited judgments. We have already spent the
            # shared decision budget, so expiration falls back without starting
            # another Jev call loop or hiding its incurred cost.
            baseline = await PairComposer().prepare_next(frozen)
            expired_proposal = self._from_composer(
                frozen, baseline, strategy, "agent_plan_expired_during_audit"
            ).model_copy(update={"provider_calls": repair.provider_calls})
            return DirectorResult(
                proposal=expired_proposal, horizon_traces=repair.traces, replan_required=True
            )
        updated = repair.horizon
        screen = updated.screens[screen_index]
        traces = repair.traces[screen_index]
        origins = {trace.origin for trace in traces}
        realized: str
        if not traces:
            realized = "none"
        elif origins == {"deterministic"}:
            realized = "deterministic"
        elif "deterministic" in origins:
            realized = "mixed"
        elif strategy == "agent_jev" and any(
            trace.reason_code in {"jev_kept_draft", "jev_local_repair"} for trace in traces
        ):
            realized = "agent_jev"
        else:
            realized = "agent_only"
        # Hard feasibility for nonzero future indexes has already excluded all
        # preceding drafts. An empty repaired screen with hard minima cannot be
        # mislabeled a valid empty screen, even when later-window supply ran out.
        minimum = sum(quota.minimum for quota in frozen.constraints.topic_quotas)
        unsatisfiable = not screen.card_refs and (
            minimum > 0 or largest_feasible_count(frozen) is None
        )
        proposal = DirectorProposal.model_validate(
            {
                "request_id": frozen.request_id,
                "input_digest": frozen.input_digest,
                "candidate_digest": digest([card.model_dump(mode="json") for card in frozen.cards]),
                "constraints_digest": digest(frozen.constraints.model_dump(mode="json")),
                "direction_ref": screen.screen_id,
                "direction": screen.direction,
                "status": "UNSATISFIABLE"
                if unsatisfiable
                else "COMPLETE"
                if len(screen.card_refs) == frozen.target_count
                else "SHORTAGE",
                "requested_strategy": strategy,
                "realized_strategy": realized,
                "target_count": frozen.target_count,
                "card_refs": screen.card_refs,
                "pairs": traces,
                "provider_calls": repair.provider_calls,
                "reason_code": "agent_draft_repaired"
                if repair.patches
                else "agent_draft_preserved",
                "plan_id": updated.plan_id,
                "plan_revision": updated.revision,
                "screen_id": screen.screen_id,
                "screen_revision": screen.revision,
                "brief": screen.brief or build_screen_brief(frozen),
                "screen_review": review_screen(frozen.cards, screen.card_refs),
            }
        )
        return DirectorResult(
            proposal=proposal,
            horizon=updated,
            patches=repair.patches,
            horizon_traces=repair.traces,
            replan_required=repair.replan_required
            or plan_needs_replanning(frozen, horizon, strategy=strategy),
        )

    @staticmethod
    def _from_composer(
        request: CompositionRequest,
        proposal: ScreenProposal,
        strategy: DirectorStrategy,
        reason: str | None = None,
    ) -> DirectorProposal:
        return DirectorProposal(
            request_id=proposal.request_id,
            input_digest=proposal.input_digest,
            candidate_digest=proposal.candidate_digest,
            constraints_digest=proposal.constraints_digest,
            direction_ref=request.direction_ref,
            direction=request.direction,
            status=proposal.status,
            requested_strategy=strategy,
            realized_strategy=proposal.realized_strategy,
            target_count=proposal.target_count,
            card_refs=proposal.card_refs,
            pairs=tuple(
                DirectorPair(
                    pair_index=pair.pair_index,
                    prefix_refs=pair.prefix_refs,
                    selected_refs=pair.selected_refs,
                    origin=pair.origin,
                    reason_code=pair.reason_code,
                    role=pair.role,
                    intent=pair.intent,
                    evidence_json=pair.evidence_json,
                )
                for pair in proposal.pairs
            ),
            provider_calls=proposal.provider_calls,
            reason_code=reason or proposal.reason_code,
            brief=proposal.brief,
            screen_review=proposal.screen_review,
        )
