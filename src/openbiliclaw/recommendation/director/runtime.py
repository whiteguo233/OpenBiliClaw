"""Application-owned lifecycle, rolling plans and atomic screen execution."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal, cast

from openbiliclaw.config import decision_routes_enabled
from openbiliclaw.decision.context_budget import resolve_jev_context
from openbiliclaw.decision.registry import build_decision_provider
from openbiliclaw.decision.runtime import BoundedDecisionProvider
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator
from openbiliclaw.discovery.semantic_filter import JevSemanticFilter
from openbiliclaw.soul.profile_views import (
    PROFILE_CONTEXT_VERSION,
    build_profile_catalog,
    select_profile_context,
)

from .composer import PairComposer, profile_context_headroom
from .controller import DirectorController, plan_needs_replanning
from .horizon_models import DirectorStrategy, HorizonPlan
from .models import (
    CompositionCard,
    CompositionRequest,
    CompositionSession,
    ScreenConstraints,
    digest,
)
from .planner import HorizonPlanner
from .policy import DIRECTOR_POLICY_VERSION
from .store import (
    BatchInstance,
    DirectorStore,
    GenerationClaim,
    SessionSnapshot,
    StoreConflict,
    candidate_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openbiliclaw.config import Config
    from openbiliclaw.decision.base import DecisionProvider
    from openbiliclaw.recommendation.engine import DirectorCandidateSnapshot, RecommendationEngine
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


class DirectorRuntime:
    """One runtime's shared decision route and durable, future-only planning window.

    Construction is explicit and only happens for configured routes. Provider
    requests never own a database transaction. Each completed screen crosses the
    store's fresh eligibility/version check and one atomic commit.
    """

    def __init__(
        self,
        config: Config,
        database: Database,
        engine: RecommendationEngine,
        llm: Any,
        *,
        provider: DecisionProvider | None = None,
        activate: bool = True,
    ) -> None:
        self.config = config
        self.database = database
        self.engine = engine
        self._closed = False
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.context_budget = (
            resolve_jev_context(
                config.decision.providers.jev.model,
                config.decision.providers.jev.context_limits,
            )
            if provider is not None or decision_routes_enabled(config)
            else None
        )
        self.context_digest = digest(
            {
                "director": asdict(config.director),
                "decision": asdict(config.decision),
                "filter": asdict(config.discovery.semantic_filter),
                "eval_scorer": config.discovery.eval_scorer,
                "jev_evaluation": asdict(config.discovery.jev_evaluation),
                "planner_route": asdict(config.llm),
                "source_policy": asdict(config.sources),
                "screen_policy_version": DIRECTOR_POLICY_VERSION,
                "profile_projection_version": PROFILE_CONTEXT_VERSION,
                "jev_context_fingerprint": self.context_budget.fingerprint
                if self.context_budget
                else "",
            }
        )
        self.store = DirectorStore(database)
        self.provider: DecisionProvider | None = provider
        if self.provider is None and decision_routes_enabled(config):
            adapter = build_decision_provider(config.decision)
            if adapter is None:
                raise ValueError("enabled decision route has no provider")
            self.provider = BoundedDecisionProvider(
                adapter, model=config.decision.providers.jev.model
            )
        self.planner = HorizonPlanner(llm)
        self.jev_evaluator = (
            JevContentEvaluator(
                self.provider,
                model=config.decision.providers.jev.model,
                config=config.discovery.jev_evaluation,
                context_budget=self.context_budget,
            )
            if config.discovery.eval_scorer == "jev" and self.provider is not None
            else None
        )
        self.controller = DirectorController(
            provider=self.provider, context_budget=self.context_budget
        )
        self.semantic_filter = (
            JevSemanticFilter(
                config.discovery.semantic_filter,
                self.provider,
                model=config.decision.providers.jev.model,
                database=database,
            )
            if config.discovery.semantic_filter.mode != "off" and self.provider is not None
            else None
        )
        if activate:
            self.activate()

    def activate(self) -> None:
        """Fence obsolete generations only when the application swaps ownership."""
        self.store.activate_context(self.context_digest)

    @property
    def enabled(self) -> bool:
        return self.config.director.mode != "off" and not self._closed

    @staticmethod
    def _deliverable(
        batch: BatchInstance | None,
        latest_disliked_topics: Callable[[], Sequence[str]] | None,
    ) -> BatchInstance | None:
        """Suppress a newly excluded whole screen without mutating its history."""
        if batch is None or latest_disliked_topics is None:
            return batch
        from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

        try:
            allowed = filter_recommendation_rows(
                batch.items,
                latest_disliked_topics(),
                restore_on_total_fuzzy_match=False,
            )
        except Exception:
            raise StoreConflict("latest_dislike_unavailable") from None
        return batch if len(allowed) == len(batch.items) else None

    async def restore(
        self,
        stream_ref: str,
        batch_ref: str | None = None,
        *,
        latest_disliked_topics: Callable[[], Sequence[str]] | None = None,
    ) -> BatchInstance | None:
        """Return the committed identity without selecting, consuming or presenting."""
        batch = await asyncio.to_thread(self.store.restore, stream_ref, batch_ref)
        return self._deliverable(batch, latest_disliked_topics)

    def _request(
        self,
        claim: GenerationClaim,
        snapshot: DirectorCandidateSnapshot,
        *,
        explicit_intent: str,
        profile: SoulProfile,
    ) -> CompositionRequest:
        session = self.store.session(claim.stream_ref)
        if (
            session.state_revision != claim.state_revision
            or session.runtime_epoch != claim.runtime_epoch
            or session.feedback_cursor != claim.feedback_cursor
        ):
            raise StoreConflict("generation_state_stale")
        return self._build_request(
            session,
            snapshot,
            request_id=claim.request_id,
            target_count=claim.target_count,
            direction_ref=claim.direction_ref,
            direction=claim.direction,
            explicit_intent=explicit_intent,
            profile=profile,
        )

    def _build_request(
        self,
        session: SessionSnapshot,
        snapshot: DirectorCandidateSnapshot,
        *,
        request_id: str,
        target_count: int,
        direction_ref: str,
        direction: str,
        explicit_intent: str,
        profile: SoulProfile,
    ) -> CompositionRequest:
        cards = tuple(
            CompositionCard(
                ref="c_" + digest(card.item_key)[:24],
                item_key=card.item_key,
                title=(card.title or "Untitled")[:200],
                summary=(card.description or card.body_text or "")[:400],
                topic_group=(card.topic_group or "unknown")[:80],
                content_style=(card.style_key or "")[:80],
                source_platform=(card.source_platform or "")[:32],
                baseline_score=snapshot.scores.get(card.item_key, card.relevance_score),
            )
            for card in snapshot.items
        )
        catalog = build_profile_catalog(profile)
        query_texts = [explicit_intent, direction]
        query_texts.extend(
            text for card in cards for text in (card.title, card.summary, card.topic_group)
        )
        query_texts.extend(event.description for event in session.real_feedback)
        # These are profile allocations, not inferred model windows. The slow
        # planner retains its existing 96k-byte complete-prompt guard. Reserve
        # space for instructions, policy, history and candidate envelopes.
        nonprofile_bytes = len(
            json.dumps(
                {
                    "cards": [card.model_dump(mode="json") for card in cards],
                    "feedback": [event.model_dump(mode="json") for event in session.real_feedback],
                    "history": [
                        screen.model_dump(mode="json") for screen in session.recent_screens
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
        slow_floor = select_profile_context(
            catalog, query_texts=(), max_bytes=0, purpose="director_planning"
        ).used_bytes
        slow_allocation = max(slow_floor, min(32_000, max(0, 96_000 - nonprofile_bytes - 16_000)))
        slow = select_profile_context(
            catalog, query_texts=query_texts, max_bytes=slow_allocation, purpose="director_planning"
        )
        fast_minimum = select_profile_context(
            catalog, query_texts=(), max_bytes=0, purpose="director_pair"
        )
        if (
            self.context_budget is not None
            and fast_minimum.used_bytes > self.context_budget.max_state_question_bytes
        ):
            # Keep all mandatory conditions in the snapshot. The complete
            # request guard records decision_input_too_large without a call.
            logger.warning("Director mandatory profile exceeds selected model context")
        request = CompositionRequest(
            request_id=request_id,
            session=CompositionSession(
                session_ref=session.session_ref,
                stream_ref=session.stream_ref,
                state_revision=session.state_revision,
                feedback_cursor=session.feedback_cursor,
                configuration_revision=session.configuration_revision,
                explicit_intent=explicit_intent,
                real_feedback=session.real_feedback,
                recent_screens=session.recent_screens,
                latest_preference_revision=session.latest_preference_revision,
                profile_context=json.loads(json.dumps(slow.payload)),
                fast_profile_context=json.loads(json.dumps(fast_minimum.payload)),
                profile_source_digest=digest(
                    {"catalog": catalog, "projection_policy": PROFILE_CONTEXT_VERSION}
                ),
            ),
            candidate_set_ref="serving_" + digest([card.model_dump(mode="json") for card in cards]),
            candidate_revision=session.state_revision,
            feature_schema_version="serving-cards.v1",
            direction_ref=direction_ref,
            direction=direction,
            cards=cards,
            constraints=ScreenConstraints(version="serving-eligibility.v1"),
            target_count=target_count,
            decision_budget_seconds=self.config.director.decision_budget_seconds,
            max_provider_calls=self.config.director.max_decision_calls,
        )
        fast_allocation = 8_000
        if self.context_budget is not None:
            fast_allocation = min(
                self.context_budget.max_state_question_bytes // 3,
                fast_minimum.used_bytes + profile_context_headroom(request, self.context_budget),
            )
        fast = select_profile_context(
            catalog,
            query_texts=query_texts,
            max_bytes=max(fast_minimum.used_bytes, fast_allocation),
            purpose="director_pair",
        )
        return request.model_copy(
            update={
                "session": request.session.model_copy(update={"fast_profile_context": fast.payload})
            }
        )

    @staticmethod
    def _remaining_plan(plan: HorizonPlan | None, consumed: set[str]) -> dict[str, Any]:
        if plan is None:
            return {}
        # A crash cannot cause a committed screen to become mutable again:
        # canonical consumed identities are removed on every subsequent read.
        screens = tuple(screen for screen in plan.screens if not (set(screen.card_refs) & consumed))
        if not screens:
            return {}
        return plan.model_copy(
            update={"screens": screens, "revision": plan.revision + 1}
        ).model_dump(mode="json")

    async def generate(
        self,
        profile: SoulProfile,
        *,
        stream_ref: str,
        request_id: str,
        intent: Literal["NEXT", "STAY"] = "NEXT",
        limit: int = 10,
        source_platform: str = "",
        excluded_keys: frozenset[str] = frozenset(),
        explicit_intent: str = "",
        latest_disliked_topics: Callable[[], Sequence[str]] | None = None,
    ) -> BatchInstance | None:
        """Generate or replay one screen; NEXT advances, STAY keeps the root direction."""
        if not self.enabled:
            raise ValueError("director is disabled")
        if intent not in {"NEXT", "STAY"}:
            raise ValueError("unsupported director intent")
        # Local lock avoids duplicate paid work; store fences arbitrate other
        # processes and requests. Cap idle stream locks to bound memory.
        if stream_ref not in self._locks and len(self._locks) >= 128:
            for key, lock in tuple(self._locks.items()):
                if not lock.locked():
                    self._locks.pop(key)
                    break
            else:
                raise StoreConflict("runtime_stream_capacity")
        async with self._locks.setdefault(stream_ref, asyncio.Lock()):
            direction = (
                explicit_intent.strip()
                or "结合当前兴趣与实际反馈，安排有衔接、适度探索的一屏内容。"
            )
            claim = await asyncio.to_thread(
                self.store.begin,
                stream_ref,
                request_id,
                intent=intent,
                context_digest=self.context_digest,
                direction_ref="direction_" + digest(direction)[:24],
                direction=direction,
                target_count=limit,
                source_platform=source_platform,
                request_digest=digest(
                    {"excluded_keys": sorted(excluded_keys), "intent_text": explicit_intent}
                ),
                lease_seconds=self.config.director.planning_budget_seconds
                + self.config.director.decision_budget_seconds
                + 30,
            )
            if isinstance(claim, BatchInstance):
                return self._deliverable(claim, latest_disliked_topics)
            try:
                snapshot = await self.engine.load_director_candidates(
                    profile,
                    limit=self.config.director.max_candidates,
                    source_platform=source_platform,
                    excluded_keys=excluded_keys | frozenset(claim.excluded_item_keys),
                )
                request = await asyncio.to_thread(
                    self._request, claim, snapshot, explicit_intent=explicit_intent, profile=profile
                )
                stored = await asyncio.to_thread(
                    self.store.load_plan, stream_ref, include_stale=True
                )
                stored_revision = stored.revision if stored else 0
                horizon = None
                planning_trace: dict[str, Any] = {
                    "source": "not_applicable",
                    "reason_code": "agent_planning_not_requested",
                    "calls": 0,
                    "completion": None,
                }
                if (
                    intent == "NEXT"
                    and stored
                    and stored.expires_at > time.time()
                    and stored.runtime_epoch == claim.runtime_epoch
                    and stored.plan
                ):
                    try:
                        horizon = HorizonPlan.model_validate_json(json.dumps(stored.plan))
                        if plan_needs_replanning(
                            request,
                            horizon,
                            strategy=cast("DirectorStrategy", self.config.director.strategy),
                        ):
                            horizon = None
                    except ValueError:
                        logger.warning("Discarding invalid persisted director plan")
                if self.config.director.strategy != "jev_recurrent" and horizon is None:
                    planning = await self.planner.plan(
                        request,
                        horizon_screens=1
                        if intent == "STAY"
                        else self.config.director.planning_horizon_screens,
                        ttl_seconds=self.config.director.plan_ttl_seconds,
                        budget_seconds=self.config.director.planning_budget_seconds,
                    )
                    horizon = planning.plan
                    planning_trace = {
                        "source": "fresh",
                        "reason_code": planning.reason_code,
                        "calls": planning.calls,
                        "completion": horizon.planning_completion.model_dump(mode="json")
                        if horizon is not None and horizon.planning_completion is not None
                        else None,
                    }
                elif self.config.director.strategy != "jev_recurrent" and horizon is not None:
                    planning_trace = {
                        "source": "cached",
                        "reason_code": "cached_agent_horizon",
                        "calls": 0,
                        "completion": horizon.planning_completion.model_dump(mode="json")
                        if horizon.planning_completion is not None
                        else None,
                    }
                result = await self.controller.prepare_next(
                    request,
                    strategy=cast("DirectorStrategy", self.config.director.strategy),
                    horizon=horizon,
                )
                proposal = result.proposal
                selected_refs = proposal.card_refs
                realized = proposal.realized_strategy
                trace = result.model_dump(mode="json")
                trace["planning"] = planning_trace
                plan_id, plan_revision, slot_seq = proposal.plan_id, proposal.plan_revision, None
                direction_ref, chosen_direction = proposal.direction_ref, proposal.direction
                if proposal.screen_id and result.horizon:
                    for index, screen in enumerate(result.horizon.screens):
                        if screen.screen_id == proposal.screen_id:
                            slot_seq = index
                            direction_ref, chosen_direction = screen.screen_id, screen.direction
                if self.config.director.mode == "shadow":
                    baseline = await PairComposer().prepare_next(request)
                    selected_refs, realized = baseline.card_refs, "deterministic"
                    trace["shadow_proposal"] = trace.pop("proposal")
                    trace["served_baseline"] = baseline.model_dump(mode="json")
                    plan_id, plan_revision, slot_seq = None, None, None
                    direction_ref, chosen_direction = claim.direction_ref, claim.direction
                if not selected_refs:
                    await asyncio.to_thread(self.store.abandon, claim, "no_legal_candidates")
                    return None
                by_ref = {card.ref: card for card in request.cards}
                raw_by_key = {str(row.get("item_key", "")): row for row in snapshot.source_rows}
                items: list[dict[str, Any]] = []
                fingerprints: dict[str, str] = {}
                for ref in selected_refs:
                    card = by_ref[ref]
                    raw = dict(raw_by_key[card.item_key])
                    fingerprints[card.item_key] = candidate_fingerprint(raw)
                    raw.update(
                        {
                            "expression": str(raw.get("pool_expression", "")),
                            "topic": str(raw.get("pool_topic_label", "")),
                            "confidence": float(raw.get("relevance_score", 0.0)),
                            "presented": 0,
                        }
                    )
                    items.append(raw)
                if self._closed:
                    raise StoreConflict("runtime_closed")
                # STAY is tied to the previously committed direction, never a
                # newly invented Agent direction even when draft contents change.
                if claim.intent == "STAY":
                    direction_ref, chosen_direction = claim.direction_ref, claim.direction
                    root = (
                        await self.restore(stream_ref, claim.root_batch_ref)
                        if self.config.director.strategy != "jev_recurrent"
                        else None
                    )
                    plan_id = root.plan_id if root else None
                    plan_revision = root.plan_revision if root else None
                    slot_seq = root.slot_seq if root else None
                remaining = self._remaining_plan(result.horizon, set(selected_refs))
                if claim.intent == "STAY":
                    # Reshuffling this direction must not consume the next
                    # screen in the original rolling plan. Remove only newly
                    # consumed collisions, preserving unaffected future drafts.
                    original = None
                    if (
                        stored
                        and stored.plan
                        and stored.expires_at > time.time()
                        and stored.runtime_epoch == claim.runtime_epoch
                    ):
                        with suppress(ValueError):
                            original = HorizonPlan.model_validate_json(json.dumps(stored.plan))
                            if original.expires_at <= time.time():
                                original = None
                    remaining = self._remaining_plan(original, set(selected_refs))
                batch = await asyncio.to_thread(
                    self.store.commit,
                    claim,
                    items,
                    requested_strategy=cast("DirectorStrategy", self.config.director.strategy),
                    realized_strategy=realized,
                    trace=trace,
                    constraints=request.constraints,
                    candidate_fingerprints=fingerprints,
                    plan_id=plan_id,
                    plan_revision=plan_revision,
                    slot_seq=slot_seq,
                    direction_ref=direction_ref,
                    direction=chosen_direction,
                    expected_plan_revision=stored_revision,
                    plan_update=remaining,
                    plan_ttl_seconds=self.config.director.plan_ttl_seconds,
                    xhs_self_nickname=self.engine._xhs_self_nickname(),
                    latest_disliked_topics=latest_disliked_topics,
                )
                if self.config.director.strategy != "jev_recurrent" and (
                    not remaining or result.replan_required
                ):
                    self._schedule_plan(
                        profile, stream_ref, source_platform, limit, explicit_intent
                    )
                return batch
            except BaseException:
                try:
                    await asyncio.to_thread(self.store.abandon, claim, "generation_failed")
                except Exception:
                    logger.warning("Director generation cleanup failed; lease remains fenced")
                raise

    def _schedule_plan(
        self,
        profile: SoulProfile,
        stream_ref: str,
        scope: str,
        limit: int,
        intent: str,
    ) -> None:
        previous = self._tasks.get(stream_ref)
        if previous is not None and not previous.done():
            return
        if self._closed or len(self._tasks) >= 8:
            return
        task = asyncio.create_task(self._plan_ahead(profile, stream_ref, scope, limit, intent))
        self._tasks[stream_ref] = task
        task.add_done_callback(
            lambda done: (
                self._tasks.pop(stream_ref, None) if self._tasks.get(stream_ref) is done else None
            )
        )

    async def _plan_ahead(
        self, profile: SoulProfile, stream: str, scope: str, limit: int, intent: str
    ) -> None:
        try:
            session = await asyncio.to_thread(self.store.session, stream)
            snapshot = await self.engine.load_director_candidates(
                profile, limit=self.config.director.max_candidates, source_platform=scope
            )
            # Planning has no generation claim, lease or reservation.
            request = self._build_request(
                session,
                snapshot,
                request_id="planning_" + str(time.time_ns()),
                target_count=limit,
                direction_ref="direction_" + digest(intent or "current")[:24],
                direction=intent or "结合当前兴趣与实际反馈安排接下来的几屏",
                explicit_intent=intent,
                profile=profile,
            )
            stored = await asyncio.to_thread(self.store.load_plan, stream, include_stale=True)
            result = await self.planner.plan(
                request,
                horizon_screens=self.config.director.planning_horizon_screens,
                ttl_seconds=self.config.director.plan_ttl_seconds,
                budget_seconds=self.config.director.planning_budget_seconds,
                background=True,
            )
            if result.plan is not None and not self._closed:
                await asyncio.to_thread(
                    self.store.save_plan,
                    stream,
                    result.plan.model_dump(mode="json"),
                    expected_revision=stored.revision if stored else 0,
                    expected_state_revision=session.state_revision,
                    expected_runtime_epoch=session.runtime_epoch,
                    ttl_seconds=self.config.director.plan_ttl_seconds,
                )
        except asyncio.CancelledError:
            raise
        except StoreConflict:
            pass  # State changed: do not cache an obsolete plan or retry endlessly.
        except Exception:
            logger.warning("Director preplanning failed; next request retains explicit fallback")

    async def aclose(self) -> None:
        """Stop local speculative work; committed screens remain restorable."""
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.provider is not None:
            await self.provider.aclose()


def build_director_runtime(
    config: Config,
    database: Database,
    engine: RecommendationEngine,
    llm: Any,
    *,
    activate: bool = True,
) -> DirectorRuntime | None:
    """No enabled route means no provider, credential lookup, or director schema write."""
    mode = getattr(getattr(config, "director", None), "mode", "off")
    filter_mode = getattr(
        getattr(getattr(config, "discovery", None), "semantic_filter", None), "mode", "off"
    )
    eval_scorer = getattr(getattr(config, "discovery", None), "eval_scorer", "llm")
    if mode == "off" and filter_mode == "off" and eval_scorer != "jev":
        return None
    return DirectorRuntime(config, database, engine, llm, activate=activate)
