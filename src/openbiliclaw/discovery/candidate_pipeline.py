"""Shared evaluator/admission pipeline for discovery candidates."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.admission import effective_admission_threshold
from openbiliclaw.discovery.candidate_pool import (
    EVALUATED,
    REJECTED_CACHE_ADMISSION,
    REJECTED_FRANCHISE_QUOTA,
    REJECTED_LOW_SCORE,
    REJECTED_RECENTLY_VIEWED,
    REJECTED_TEMPORAL_STALE,
    DiscoveryCandidateWrite,
    discovered_content_to_candidate_write,
    discovery_candidate_pending_cap,
    row_to_discovered_content,
)
from openbiliclaw.discovery.temporal import evaluate_temporal_eligibility
from openbiliclaw.llm.base import classify_llm_unavailability
from openbiliclaw.sources.platforms import source_family as _source_family

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent

logger = logging.getLogger(__name__)
_DEFAULT_EVAL_BATCH_SIZE = 45
# A claim older than this is a dead evaluator (task crashed / cancelled
# without transitioning the row) — re-queue it. Restart orphans don't wait
# this long: the first pipeline of the process sweeps them all (see
# __post_init__).
_STALE_EVAL_CLAIM_MINUTES = 30
# Process-level: only the FIRST pipeline built in this process releases all
# 'evaluating' rows. Config-reload rebuilds skip it so an in-flight batch
# from the previous pipeline instance isn't needlessly re-queued.
_orphan_eval_claims_released = False


def _default_score_thresholds() -> dict[str, float]:
    return {
        "search": 0.60,
        "trending": 0.60,
        "hot": 0.60,
        "related": 0.60,
        "related_chain": 0.60,
        "explore": 0.58,
        "feed": 0.60,
        "backfill": 0.60,
        "default": 0.60,
    }


@dataclass(frozen=True)
class CandidateEvalClaim:
    """One token-owned batch claimed for LLM evaluation."""

    token: str
    rows: tuple[dict[str, Any], ...]
    items: tuple[DiscoveredContent, ...]


@dataclass(frozen=True)
class CandidateEvalOutcome:
    """LLM-only output awaiting serialized persistence and admission."""

    claim: CandidateEvalClaim
    scores: tuple[float, ...]
    elapsed_seconds: float


class PostAdmissionCopyState(StrEnum):
    """Terminal ownership state for copy work following a candidate admission."""

    NOT_OWNED = "not_owned"
    CALLBACK_COMPLETED = "callback_completed"
    CALLBACK_FAILED = "callback_failed"


@dataclass(frozen=True)
class PostAdmissionCopyReceipt:
    """Describe whether an admission callback already owned the copy stage.

    The result travels with one ``drain_pending`` return value so callers can
    distinguish "there was no admission callback" from "the callback already
    ran (including a durable, retryable failure)".  That is important for
    one-shot runtimes: their refresh-plan cleanup must only act as a fallback,
    never re-invoke the owner for the same admission.
    """

    state: PostAdmissionCopyState = PostAdmissionCopyState.NOT_OWNED
    completed: int = 0

    @property
    def owns_copy_stage(self) -> bool:
        """Whether a post-admission callback took ownership this drain."""

        return self.state is not PostAdmissionCopyState.NOT_OWNED


class CandidateDrainResult(dict[str, int]):
    """Mapping-compatible drain metrics with an explicit copy ownership receipt.

    Existing consumers treat drain output as a ``dict[str, int]``.  Keeping
    that mapping shape preserves their metric contract (and legacy equality
    checks) while exposing the non-metric ownership result as a dedicated
    attribute instead of smuggling it into the public count payload.
    """

    def __init__(
        self,
        values: dict[str, int],
        *,
        post_admission_copy: PostAdmissionCopyReceipt,
    ) -> None:
        super().__init__(values)
        self.post_admission_copy = post_admission_copy


@dataclass
class DiscoveryCandidatePipeline:
    """Drain pending raw candidates through one mixed-source evaluator."""

    database: Any
    discovery_engine: ContentDiscoveryEngine
    pool_target_count: int = 300
    score_thresholds: dict[str, float] = field(default_factory=_default_score_thresholds)
    admission_min_score: float = 0.60
    xhs_self_nickname: str = ""
    xhs_self_nickname_provider: Callable[[], str] | None = None
    max_eval_attempts: int = 5
    max_batch_eval_attempts: int = 50
    min_eval_batch_size: int = 1
    max_eval_wait_seconds: float = 0.0
    candidate_fetch_oversample: int = 1
    max_supply_fill_attempts: int = 3
    max_supply_fill_seconds: float = 240.0
    eval_batch_concurrency: int = 2
    on_candidates_enqueued: Callable[[int], None] | None = None
    # Non-daemon callers may own the final, durable expression-copy stage
    # inline.  The API runtime leaves this unset because its coordinator owns
    # that stage after claim completion.
    on_candidates_admitted: Callable[[Any, int], Any] | None = None
    # Pool-share fairness (spec 2026-07-20, Phase 2): optional provider of the
    # per-family visible-pool targets ``{source_family: target}``. When set,
    # ``_admit_until_full`` becomes share-aware (under-share rows admitted
    # first, over-share rows only as an availability fallback). ``None`` keeps
    # the legacy global-cap-only FIFO admission byte-for-byte (invariant 5).
    source_share_targets: Callable[[], dict[str, int]] | None = None
    time_fn: Callable[[], float] = field(default=time.monotonic, repr=False)
    _drain_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    _first_pending_eval_seen_at: float | None = field(
        default=None,
        init=False,
        repr=False,
    )
    last_admitted_items: list[DiscoveredContent] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        # The evaluator lives in-process, so any 'evaluating' row found at
        # process start is orphaned by a crash/restart. Database.initialize()
        # only resets claims already ≥30 min stale — restart orphans are
        # typically seconds old, slipped through, and nothing swept later:
        # they count toward the supply target ("target_reached") but the
        # drain only claims 'pending_eval' and enforce_pool_cap never deletes
        # in-flight rows, so the pool starves indefinitely (field log
        # 2026-07-05: 40 immortal rows, pool_available=0 for good).
        global _orphan_eval_claims_released
        if _orphan_eval_claims_released:
            return
        _orphan_eval_claims_released = True
        released = self._release_stale_eval_claims(max_age_minutes=0)
        if released:
            logger.warning(
                "released %s orphaned 'evaluating' claim(s) left by a previous run; "
                "re-queued as pending_eval",
                released,
            )

    def _release_stale_eval_claims(self, *, max_age_minutes: int) -> int:
        reset = getattr(self.database, "reset_stale_discovery_candidate_evaluations", None)
        if not callable(reset):
            return 0
        try:
            return int(reset(max_age_minutes=max_age_minutes) or 0)
        except Exception:
            logger.debug("stale evaluating claim release failed", exc_info=True)
            return 0

    def enqueue_candidates(
        self,
        items: list[DiscoveredContent],
        *,
        source_context: str = "",
    ) -> int:
        """Normalize and enqueue discovered raw items into the candidate pool."""

        writes: list[DiscoveryCandidateWrite] = [
            discovered_content_to_candidate_write(item, source_context=source_context)
            for item in items
        ]
        writes, diagnostics, duplicate_only_keyword_ids = self._filter_known_writes(writes)
        if diagnostics["input"] != diagnostics["kept"]:
            logger.info(
                "candidate enqueue prefilter: input=%s kept=%s duplicate_in_batch=%s "
                "known_candidate=%s known_cache=%s",
                diagnostics["input"],
                diagnostics["kept"],
                diagnostics["duplicate_in_batch"],
                diagnostics["known_candidate"],
                diagnostics["known_cache"],
            )
        if duplicate_only_keyword_ids:
            retire = getattr(self.database, "retire_duplicate_only_keywords", None)
            if callable(retire):
                try:
                    retired = int(retire(sorted(duplicate_only_keyword_ids)) or 0)
                except Exception:
                    logger.debug("duplicate-only keyword retirement failed", exc_info=True)
                else:
                    if retired:
                        logger.info(
                            "candidate prefilter retired %d duplicate-only keyword(s)",
                            retired,
                        )
        if not writes:
            return 0
        enqueue = self.database.enqueue_discovery_candidates
        cap = self._max_pending_per_source()
        if cap is None:
            inserted = int(enqueue(writes))
        else:
            try:
                inserted = int(enqueue(writes, max_pending_per_source=cap))
            except TypeError:
                inserted = int(enqueue(writes))
        if inserted > 0 and self.on_candidates_enqueued is not None:
            self.on_candidates_enqueued(inserted)
        return inserted

    async def ensure_pending_supply(
        self,
        *,
        profile: Any,
        strategies: list[str],
        limit: int,
        target_pending: int | None = None,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
        keywords: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
        max_attempts: int | None = None,
        max_seconds: float | None = None,
    ) -> dict[str, int | str]:
        """Produce raw candidates until the Evo input queue reaches a waterline."""

        requested = max(0, int(limit))
        target = max(0, int(target_pending if target_pending is not None else requested))
        if requested <= 0 or target <= 0:
            pending, evaluating = self._eval_supply_counts()
            return {
                "inserted": 0,
                "attempts": 0,
                "pending_eval": pending,
                "evaluating": evaluating,
                "reason": "no_target",
            }
        if self.pool_full():
            pending, evaluating = self._eval_supply_counts()
            return {
                "inserted": 0,
                "attempts": 0,
                "pending_eval": pending,
                "evaluating": evaluating,
                "reason": "pool_full",
            }

        attempts_limit = max(
            1,
            int(max_attempts if max_attempts is not None else self.max_supply_fill_attempts),
        )
        seconds_limit = max(
            0.0,
            float(max_seconds if max_seconds is not None else self.max_supply_fill_seconds),
        )
        started = float(self.time_fn())
        inserted_total = 0
        attempts = 0
        stop_reason = "attempts_exhausted"

        while attempts < attempts_limit:
            pending, evaluating = self._eval_supply_counts()
            active = pending + evaluating
            if active >= target:
                stop_reason = "target_reached"
                break
            if self.pool_full():
                stop_reason = "pool_full"
                break
            elapsed = max(0.0, float(self.time_fn()) - started)
            if seconds_limit > 0 and elapsed >= seconds_limit:
                stop_reason = "time_budget_exhausted"
                break

            missing = max(1, target - active)
            base_limit = max(requested, missing)
            request_limit = min(base_limit * (attempts + 1), max(base_limit, 120))
            logger.info(
                "candidate supply fill attempt: attempt=%s target=%s pending=%s "
                "evaluating=%s request_limit=%s strategies=%s",
                attempts + 1,
                target,
                pending,
                evaluating,
                request_limit,
                ",".join(strategies),
            )
            inserted = await self.produce_and_enqueue(
                profile=profile,
                strategies=strategies,
                limit=request_limit,
                strategy_limits=strategy_limits,
                pool_snapshot=pool_snapshot,
                keywords=keywords,
                keyword_ids=keyword_ids,
            )
            attempts += 1
            inserted_total += int(inserted or 0)
            after_pending, after_evaluating = self._eval_supply_counts()
            logger.info(
                "candidate supply fill result: attempt=%s inserted=%s pending=%s evaluating=%s",
                attempts,
                inserted,
                after_pending,
                after_evaluating,
            )
        pending, evaluating = self._eval_supply_counts()
        if pending + evaluating >= target:
            stop_reason = "target_reached"
        logger.info(
            "candidate supply fill done: reason=%s attempts=%s inserted=%s "
            "pending=%s evaluating=%s target=%s",
            stop_reason,
            attempts,
            inserted_total,
            pending,
            evaluating,
            target,
        )
        return {
            "inserted": inserted_total,
            "attempts": attempts,
            "pending_eval": pending,
            "evaluating": evaluating,
            "reason": stop_reason,
        }

    async def produce_and_enqueue(
        self,
        *,
        profile: Any,
        strategies: list[str],
        limit: int,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
        keywords: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
    ) -> int:
        """Fetch raw candidates with the discovery engine and enqueue them.

        ``keywords`` (when provided) is forwarded to search sub-strategies that
        accept it — the unified keyword planner injection point. ``None`` keeps
        the legacy self-generating behavior. Only forwarded when non-None so
        engines/stubs without a ``keywords`` kwarg stay byte-compatible.

        ``keyword_ids`` (P1.8) is the parallel ``keyword text → keyword id`` map
        forwarded alongside ``keywords`` so each produced candidate carries its
        producing word's ``source_keyword_id`` for admit-time yield backfill.
        Only forwarded when truthy, so the flag-off path stays byte-compatible.
        """

        if self.pool_full():
            return 0

        extra: dict[str, Any] = {}
        if keywords is not None:
            extra["keywords"] = keywords
        if keyword_ids:
            extra["keyword_ids"] = keyword_ids

        produce_limit = self._oversampled_produce_limit(limit)
        produce_strategy_limits = self._oversampled_strategy_limits(
            strategy_limits,
            requested_limit=limit,
            produce_limit=produce_limit,
        )

        produce_fn = getattr(self.discovery_engine, "produce_candidates", None)
        if callable(produce_fn):
            items = await produce_fn(
                profile,
                strategies=strategies,
                limit=produce_limit,
                strategy_limits=produce_strategy_limits,
                pool_snapshot=pool_snapshot,
                **extra,
            )
        else:
            items = await self.discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=produce_limit,
                strategy_limits=produce_strategy_limits,
                pool_snapshot=pool_snapshot,
                **extra,
            )
        return self.enqueue_candidates(list(items), source_context="mixed")

    async def drain_pending(
        self,
        *,
        profile: Any,
        batch_size: int = _DEFAULT_EVAL_BATCH_SIZE,
        flush: bool = False,
    ) -> CandidateDrainResult:
        """Evaluate one batch; explicit init may flush the coalescing delay only."""

        if self._drain_lock.locked():
            self.last_admitted_items = []
            return CandidateDrainResult(
                {"evaluated": 0, "cached": 0, "rejected": 0},
                post_admission_copy=PostAdmissionCopyReceipt(),
            )
        async with self._drain_lock:
            result = await self._drain_pending_locked(
                profile=profile, batch_size=batch_size, flush=flush
            )
        post_admission_copy = await self._notify_candidates_admitted(
            profile=profile,
            admitted=int(result.get("cached", 0) or 0),
        )
        return CandidateDrainResult(result, post_admission_copy=post_admission_copy)

    async def _notify_candidates_admitted(
        self,
        *,
        profile: Any,
        admitted: int,
    ) -> PostAdmissionCopyReceipt:
        """Run the optional post-admission owner after its DB commit.

        Candidate evaluation owns only raw evaluation and cache admission.  A
        non-daemon composition can supply the final copy owner here, after the
        candidate transaction is durable; failures remain retryable because
        un-copied rows stay in the durable pending-copy set.
        """

        callback = self.on_candidates_admitted
        if admitted <= 0 or callback is None:
            return PostAdmissionCopyReceipt()
        try:
            result = callback(profile, admitted)
            if inspect.isawaitable(result):
                result = await result
            try:
                completed = max(0, int(result or 0))
            except (TypeError, ValueError):
                completed = 0
            return PostAdmissionCopyReceipt(
                state=PostAdmissionCopyState.CALLBACK_COMPLETED,
                completed=completed,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("post-admission candidate callback failed")
            # The durable pending-copy rows remain retryable on a later
            # operation. The callback still owns this admission, so callers
            # must not immediately invoke it a second time as a cleanup pass.
            return PostAdmissionCopyReceipt(
                state=PostAdmissionCopyState.CALLBACK_FAILED,
            )

    def claim_batch(self, *, limit: int) -> CandidateEvalClaim | None:
        """Claim one token-owned batch without performing LLM work."""

        claim_limit = max(0, int(limit))
        if claim_limit <= 0:
            return None
        token = secrets.token_hex(16)
        claim_fn = self.database.claim_discovery_candidates_for_eval
        # Pool-share fairness (spec 2026-07-20, Phase 8): claim under-share
        # sources' pending rows first so the evaluator isn't burned on an
        # over-supplied backlog that admission won't seat. Empty list / older DB
        # (TypeError) → legacy claim order.
        preferred = self._under_share_platforms()
        try:
            rows = list(
                claim_fn(
                    limit=claim_limit,
                    claim_token=token,
                    preferred_source_platforms=preferred,
                )
            )
        except TypeError:
            try:
                rows = list(claim_fn(limit=claim_limit, claim_token=token))
            except TypeError:
                rows = list(claim_fn(limit=claim_limit))
        if not rows:
            return None
        stored_tokens = {str(row.get("claim_token") or "") for row in rows}
        stored_tokens.discard("")
        if len(stored_tokens) == 1:
            token = stored_tokens.pop()
        items = tuple(row_to_discovered_content(row) for row in rows)
        return CandidateEvalClaim(token=token, rows=tuple(rows), items=items)

    def claim_ready_batch(self, *, limit: int) -> CandidateEvalClaim | None:
        """Claim only when the configured coalescing window is ready."""

        batch_size = self._effective_batch_size(limit)
        if batch_size <= 0:
            return None
        if self._waiting_pending_eval_count(batch_size) is not None:
            return None
        return self.claim_batch(limit=batch_size)

    def eval_ready_in_seconds(self, *, limit: int) -> float:
        """Return the remaining coalescing delay for the current pending rows."""

        batch_size = self._effective_batch_size(limit)
        if batch_size <= 0:
            return 0.0
        pending_count = self._pending_eval_count()
        if pending_count is None or pending_count <= 0:
            return 0.0
        min_batch = min(max(1, int(self.min_eval_batch_size)), batch_size)
        if min_batch <= 1 or pending_count >= min_batch:
            return 0.0
        max_wait = max(0.0, float(self.max_eval_wait_seconds or 0.0))
        if max_wait <= 0:
            return 0.0
        first_seen = self._first_pending_eval_seen_at
        if first_seen is None:
            return max_wait
        return max(0.0, max_wait - max(0.0, float(self.time_fn()) - first_seen))

    async def evaluate_claim(self, claim: CandidateEvalClaim, profile: Any) -> CandidateEvalOutcome:
        """Run only the LLM evaluation stage; this method performs no writes."""

        started = self.time_fn()
        scores = await self.discovery_engine.evaluate_content_batch(
            list(claim.items),
            profile,
            source_context="mixed",
            batch_size=len(claim.items),
        )
        if len(scores) != len(claim.items):
            raise ValueError(
                f"evaluation returned {len(scores)} scores for {len(claim.items)} candidates"
            )
        return CandidateEvalOutcome(
            claim=claim,
            scores=tuple(float(score) for score in scores),
            elapsed_seconds=max(0.0, self.time_fn() - started),
        )

    async def complete_claim(
        self,
        outcome: CandidateEvalOutcome,
        *,
        admission_limit: int | None = None,
    ) -> dict[str, int]:
        """Persist and admit one completed claim while honoring token ownership."""

        claim = outcome.claim
        rows = list(claim.rows)
        items = list(claim.items)
        scores = list(outcome.scores)
        await self._normalize_evaluated_items(items)
        recently_viewed = self._recent_viewed_content_keys()
        updated_ids = self._persist_evaluations(
            rows,
            items,
            scores,
            recently_viewed=recently_viewed,
            claim_token=claim.token,
        )
        durable_rows = self._durable_candidate_rows(updated_ids)
        durable_by_id = {int(row["id"]): row for row in durable_rows}

        accepted: list[tuple[dict[str, Any], DiscoveredContent]] = []
        rejected = 0
        for row, _item, _score in zip(rows, items, scores, strict=True):
            candidate_id = int(row["id"])
            if candidate_id not in updated_ids:
                continue
            durable_row = durable_by_id.get(candidate_id)
            if durable_row is None:
                continue
            # A successful write does not imply admission eligibility. Storage
            # may have atomically translated the evaluator result into a
            # review lease or terminal rejection after grounding it against
            # older evidence. Only its final durable status can open the gate.
            if str(durable_row.get("status") or "") != EVALUATED:
                rejected += 1
                continue
            item = row_to_discovered_content(durable_row)
            final_score = float(item.relevance_score or 0.0)
            if self._is_recently_viewed(item, recently_viewed):
                rejected += 1
                continue
            temporal = evaluate_temporal_eligibility(
                temporal_class=item.temporal_class,
                temporal_confidence=item.temporal_confidence,
                published_at=item.published_at,
                temporal_validity_mode=item.temporal_validity_mode,
                temporal_valid_until=item.temporal_valid_until,
                temporal_scope=item.temporal_scope,
                temporal_evidence=item.temporal_evidence,
                temporal_state=item.temporal_state,
                temporal_next_review_at=item.temporal_next_review_at,
                temporal_evaluated_at=item.temporal_evaluated_at,
                temporal_policy_version=item.temporal_policy_version,
                evidence_complete=item.temporal_evidence_complete,
            )
            if temporal.hard_expired:
                self.database.reject_discovery_candidate(
                    candidate_id,
                    status=REJECTED_TEMPORAL_STALE,
                    reason=temporal.rejection_reason,
                )
                rejected += 1
                continue
            if temporal.needs_review:
                self._requeue_temporal_review(candidate_id, temporal.reason)
                rejected += 1
                continue
            if final_score < self._threshold_for(durable_row):
                rejected += 1
                continue
            accepted.append((durable_row, item))

        admitted_items: list[DiscoveredContent] = []
        cached, admission_rejected = self._admit_until_full(
            accepted,
            recently_viewed=recently_viewed,
            admitted_items=admitted_items,
            limit=admission_limit,
        )
        self.last_admitted_items = list(admitted_items)
        confirmed_ids = set(durable_by_id)
        return {
            "evaluated": len(confirmed_ids),
            "cached": cached,
            "rejected": rejected + admission_rejected,
            "stale": len(rows) - len(confirmed_ids),
        }

    def release_claim(
        self,
        claim: CandidateEvalClaim,
        *,
        reason: str,
        increment_attempts: bool = False,
    ) -> int:
        """Release only rows still owned by this claim token."""

        ids = [int(row["id"]) for row in claim.rows if int(row.get("id") or 0) > 0]
        if not ids:
            return 0
        reset_claimed = getattr(
            self.database,
            "reset_claimed_discovery_candidates_to_pending",
            None,
        )
        if callable(reset_claimed):
            return int(
                reset_claimed(
                    ids,
                    claim_token=claim.token,
                    reason=reason,
                    max_attempts=self.max_eval_attempts,
                    max_batch_attempts=self.max_batch_eval_attempts,
                    increment_attempts=increment_attempts,
                )
                or 0
            )
        self._release_eval_claims(
            list(claim.rows),
            reason=reason,
            increment_attempts=increment_attempts,
        )
        return len(ids)

    def revive_failed_eval_candidates(self) -> int:
        """Re-queue dead-lettered candidates after an evaluator recovery.

        Delegates to storage with the same getattr guard style as the other
        durable hooks so test doubles without the method keep working.
        """

        revive = getattr(self.database, "revive_failed_eval_candidates", None)
        if not callable(revive):
            return 0
        try:
            return int(revive() or 0)
        except Exception:
            logger.debug("failed_eval candidate revival failed", exc_info=True)
            return 0

    async def _drain_pending_locked(
        self,
        *,
        profile: Any,
        batch_size: int = _DEFAULT_EVAL_BATCH_SIZE,
        flush: bool = False,
    ) -> dict[str, int]:
        """Evaluate one pending batch while the shared drain lock is held."""

        # Heal mid-run stalls: an eval task that died without transitioning
        # its rows leaves 'evaluating' claims no one will ever finish. The
        # periodic drain tick is the natural sweep point — after the age
        # threshold they rejoin 'pending_eval' and get claimed below.
        released = self._release_stale_eval_claims(max_age_minutes=_STALE_EVAL_CLAIM_MINUTES)
        if released:
            logger.warning(
                "released %s stale 'evaluating' claim(s) (older than %s min); "
                "re-queued as pending_eval",
                released,
                _STALE_EVAL_CLAIM_MINUTES,
            )

        self.last_admitted_items = []
        requested = max(0, int(batch_size))
        batch_size = self._effective_batch_size(batch_size)
        if flush:
            # Preserve caller and engine caps, but do not inflate an explicit
            # first-pool wave to the daemon's minimum coalescing batch size.
            batch_size = min(requested, batch_size)
        claim_limit = self._effective_eval_claim_limit(batch_size)
        if batch_size <= 0:
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        if self._pool_full():
            return {"evaluated": 0, "cached": 0, "rejected": 0}

        recently_viewed = self._recent_viewed_content_keys()
        admitted_items: list[DiscoveredContent] = []
        retry_cached, retry_rejected = self._admit_evaluated_candidates(
            limit=claim_limit,
            recently_viewed=recently_viewed,
            admitted_items=admitted_items,
        )
        if self._pool_full():
            self.last_admitted_items = list(admitted_items)
            return {"evaluated": 0, "cached": retry_cached, "rejected": retry_rejected}

        waiting_pending = None if flush else self._waiting_pending_eval_count(batch_size)
        if waiting_pending is not None:
            self.last_admitted_items = list(admitted_items)
            return {
                "evaluated": 0,
                "cached": retry_cached,
                "rejected": retry_rejected,
                "waiting": waiting_pending,
            }

        claim = self.claim_batch(limit=claim_limit)
        if claim is None:
            self._first_pending_eval_seen_at = None
            self.last_admitted_items = list(admitted_items)
            return {"evaluated": 0, "cached": retry_cached, "rejected": retry_rejected}

        try:
            outcome = await self.evaluate_claim(claim, profile)
            result = await self.complete_claim(outcome)
        except asyncio.CancelledError:
            logger.info("discovery candidate batch evaluation cancelled; releasing claims")
            self.release_claim(claim, reason="evaluation cancelled", increment_attempts=False)
            self.last_admitted_items = list(admitted_items)
            raise
        except Exception as exc:
            # Expected-transient LLM outages (provider rate-limit cooldown, or
            # no chat provider configured yet during guided init) are retried on
            # the next drain tick by design — log one calm line, mirroring
            # engine.py's "propagating transient failure so callers can retry
            # later" style, instead of an ERROR+traceback.
            kind = classify_llm_unavailability(exc)
            if kind is not None:
                logger.warning(
                    "discovery candidate batch evaluation deferred (%s) for %d "
                    "candidate(s); releasing claims to retry on the next tick: %s",
                    kind,
                    len(claim.rows),
                    exc,
                )
            else:
                logger.exception("discovery candidate batch evaluation failed")
            self.release_claim(claim, reason=str(exc), increment_attempts=False)
            self.last_admitted_items = list(admitted_items)
            return {
                "evaluated": 0,
                "cached": retry_cached,
                "rejected": retry_rejected,
                "failed": len(claim.rows),
            }
        completed_items = list(self.last_admitted_items)
        self.last_admitted_items = [*admitted_items, *completed_items]
        return {
            "evaluated": result["evaluated"],
            "cached": retry_cached + result["cached"],
            "rejected": retry_rejected + result["rejected"],
        }

    def _effective_eval_batch_concurrency(self) -> int:
        try:
            configured = int(self.eval_batch_concurrency)
        except (TypeError, ValueError):
            configured = 2
        return max(1, min(16, configured))

    def _effective_eval_claim_limit(self, batch_size: int) -> int:
        claim_limit = max(0, int(batch_size)) * self._effective_eval_batch_concurrency()
        hard_cap = int(getattr(self.discovery_engine, "_EVALUATE_BATCH_HARD_CAP", 0) or 0)
        if hard_cap > 0:
            return min(claim_limit, hard_cap)
        return claim_limit

    def pool_full(self) -> bool:
        """Return whether the visible recommendation pool is at target."""

        return self._pool_full()

    def pool_full_for_source(self, source_family: str | None = None) -> bool:
        """Share-aware pool fullness for one producer's own source family.

        Pool-share fairness (spec 2026-07-20, Phase 5 / D6). A producer's
        internal ``pool_full`` gate used the *global* pool, which dead-locked
        under-share sources: bangumi could never produce while the pool was
        full → no bangumi ``evaluated`` supply → the rebalancer (which only
        evicts when an under-share source has supply waiting) never fired → the
        pool stayed full forever. This gate breaks the loop: when a share
        strategy is injected and the given family is below its own share, it
        returns ``False`` even at a full global pool (two-round admission +
        rebalance will free a slot); an at/over-share family (or no family)
        falls back to the global判定. Without a strategy it is identical to
        :meth:`pool_full` (invariant 5).
        """

        if not self._pool_full():
            return False
        family = str(source_family or "").strip()
        if not family:
            return True
        targets = self._share_targets()
        if targets is None:
            return True
        normalized = _source_family(family, family)
        available = self._available_by_family()
        # Under its own share → not full (admission + rebalance will free a
        # slot); at/over share → defer to the global full判定 (True here).
        return int(available.get(normalized, 0)) >= int(targets.get(normalized, 0))

    def admit_evaluated(self, *, limit: int) -> dict[str, int]:
        """Admit previously evaluated rows before spending another LLM call."""

        cleanup = getattr(
            self.database,
            "reject_temporally_stale_evaluated_candidates",
            None,
        )
        stale_rejected = int(cleanup() or 0) if callable(cleanup) else 0
        admitted_items: list[DiscoveredContent] = []
        cached, rejected = self._admit_evaluated_candidates(
            limit=max(0, int(limit)),
            recently_viewed=self._recent_viewed_content_keys(),
            admitted_items=admitted_items,
        )
        self.last_admitted_items = admitted_items
        return {"cached": cached, "rejected": stale_rejected + rejected}

    def _admit_evaluated_candidates(
        self,
        *,
        limit: int,
        recently_viewed: set[str],
        admitted_items: list[DiscoveredContent],
    ) -> tuple[int, int]:
        get_rows = getattr(self.database, "get_evaluated_discovery_candidates_for_admission", None)
        if not callable(get_rows):
            return 0, 0
        preferred = self._under_share_platforms()
        try:
            try:
                rows = list(get_rows(limit=limit, preferred_source_platforms=preferred))
            except TypeError:
                rows = list(get_rows(limit=limit))
        except Exception:
            logger.debug("evaluated discovery candidates unavailable", exc_info=True)
            return 0, 0
        if not rows:
            return 0, 0
        accepted = [(dict(row), row_to_discovered_content(dict(row))) for row in rows]
        return self._admit_until_full(
            accepted,
            recently_viewed=recently_viewed,
            admitted_items=admitted_items,
            limit=limit,
        )

    def _release_eval_claims(
        self,
        rows: list[dict[str, Any]],
        *,
        reason: str,
        increment_attempts: bool = False,
    ) -> None:
        ids = [int(row["id"]) for row in rows if int(row.get("id") or 0) > 0]
        if not ids:
            return
        reset_fn = getattr(self.database, "reset_discovery_candidates_to_pending", None)
        if callable(reset_fn):
            try:
                reset_fn(
                    ids,
                    reason=reason,
                    max_attempts=self.max_eval_attempts,
                    max_batch_attempts=self.max_batch_eval_attempts,
                    increment_attempts=increment_attempts,
                )
            except TypeError:
                reset_fn(ids, reason=reason, max_attempts=self.max_eval_attempts)
            return
        logger.debug("database does not support discovery candidate eval release")

    async def _normalize_evaluated_items(self, items: list[DiscoveredContent]) -> None:
        normalize_fn = getattr(self.discovery_engine, "normalize_evaluated_results", None)
        if callable(normalize_fn):
            result = normalize_fn(items)
            if inspect.isawaitable(result):
                await result
            return

        group_fn = getattr(self.discovery_engine, "_normalize_topic_groups", None)
        if callable(group_fn):
            result = group_fn(items)
            if inspect.isawaitable(result):
                await result
        key_fn = getattr(self.discovery_engine, "_normalize_topic_keys", None)
        if callable(key_fn):
            result = key_fn(items)
            if inspect.isawaitable(result):
                await result

    def _persist_evaluations(
        self,
        rows: list[dict[str, Any]],
        items: list[DiscoveredContent],
        scores: list[float],
        *,
        recently_viewed: set[str],
        claim_token: str | None = None,
    ) -> set[int]:
        evaluations: list[dict[str, Any]] = []
        unresolved_ids: list[int] = []
        for row, item, score in zip(rows, items, scores, strict=True):
            if item.relevance_reason == "evaluation_response_missing":
                unresolved_ids.append(int(row["id"]))
                continue
            final_score = float(item.relevance_score or score or 0.0)
            status = "evaluated"
            eval_error = ""
            if self._is_recently_viewed(item, recently_viewed):
                status = REJECTED_RECENTLY_VIEWED
                eval_error = "recently viewed"
            else:
                temporal = evaluate_temporal_eligibility(
                    temporal_class=item.temporal_class,
                    temporal_confidence=item.temporal_confidence,
                    published_at=item.published_at,
                    temporal_validity_mode=item.temporal_validity_mode,
                    temporal_valid_until=item.temporal_valid_until,
                    temporal_scope=item.temporal_scope,
                    temporal_evidence=item.temporal_evidence,
                    temporal_state=item.temporal_state,
                    temporal_next_review_at=item.temporal_next_review_at,
                    temporal_evaluated_at=item.temporal_evaluated_at,
                    temporal_policy_version=item.temporal_policy_version,
                    evidence_complete=item.temporal_evidence_complete,
                )
                if temporal.hard_expired:
                    status = REJECTED_TEMPORAL_STALE
                    eval_error = temporal.rejection_reason
                elif temporal.needs_review:
                    # Canonical storage atomically translates this evaluated
                    # result back to pending_eval. Older adapters are handled
                    # by complete/admission's explicit requeue fallback.
                    eval_error = temporal.reason
            if status == "evaluated" and final_score < self._threshold_for(row):
                status = REJECTED_LOW_SCORE
                eval_error = f"score {final_score:.2f} below threshold"
            evaluations.append(
                {
                    "candidate_id": int(row["id"]),
                    "status": status,
                    "relevance_score": final_score,
                    "relevance_reason": item.relevance_reason,
                    "temporal_class": item.temporal_class,
                    "temporal_confidence": item.temporal_confidence,
                    "temporal_reason": item.temporal_reason,
                    "temporal_policy_version": item.temporal_policy_version,
                    "temporal_validity_mode": item.temporal_validity_mode,
                    "temporal_valid_until": item.temporal_valid_until,
                    "temporal_scope": item.temporal_scope,
                    "temporal_evidence": item.temporal_evidence,
                    "temporal_state": item.temporal_state,
                    "temporal_next_review_at": item.temporal_next_review_at,
                    "temporal_evaluated_at": item.temporal_evaluated_at,
                    "temporal_evidence_complete": item.temporal_evidence_complete,
                    "topic_key": item.topic_key,
                    "topic_group": item.topic_group,
                    "style_key": item.style_key,
                    "franchise_key": item.franchise_key,
                    "pool_expression": item.pool_expression,
                    "pool_topic_label": item.pool_topic_label,
                    "eval_error": eval_error,
                }
            )
        persist_claimed = getattr(
            self.database,
            "persist_claimed_discovery_candidate_evaluations",
            None,
        )
        if claim_token is not None and callable(persist_claimed):
            updated_ids = set(persist_claimed(evaluations, claim_token=claim_token))
            reset_claimed = getattr(
                self.database, "reset_claimed_discovery_candidates_to_pending", None
            )
            if unresolved_ids and callable(reset_claimed):
                reset_claimed(
                    unresolved_ids,
                    claim_token=claim_token,
                    reason="evaluation_response_missing",
                    max_attempts=self.max_eval_attempts,
                    max_batch_attempts=self.max_batch_eval_attempts,
                    increment_attempts=False,
                )
            return updated_ids
        updated = int(self.database.update_discovery_candidate_evaluations(evaluations) or 0)
        if updated == len(evaluations):
            return {int(evaluation["candidate_id"]) for evaluation in evaluations}
        return set()

    def _durable_candidate_rows(self, candidate_ids: set[int]) -> list[dict[str, Any]]:
        """Reload persisted rows before admission, failing closed if unavailable."""

        if not candidate_ids:
            return []
        get_rows = getattr(self.database, "get_discovery_candidates_by_ids", None)
        if not callable(get_rows):
            logger.error(
                "durable candidate reload unavailable; withholding %d admission(s)",
                len(candidate_ids),
            )
            return []
        try:
            rows = get_rows(sorted(candidate_ids))
        except Exception:
            logger.exception("durable candidate reload failed; withholding admission")
            return []
        return [dict(row) for row in rows if int(row.get("id") or 0) in candidate_ids]

    def _admit_until_full(
        self,
        accepted: list[tuple[dict[str, Any], DiscoveredContent]],
        *,
        recently_viewed: set[str],
        admitted_items: list[DiscoveredContent],
        limit: int | None = None,
    ) -> tuple[int, int]:
        cache_limit = None if limit is None else max(0, int(limit))
        targets = self._share_targets()
        if targets is None:
            # Legacy path (invariant 5): global-cap-only FIFO admission.
            return self._admit_rows(
                accepted,
                recently_viewed=recently_viewed,
                admitted_items=admitted_items,
                cache_limit=cache_limit,
            )
        return self._admit_share_aware(
            accepted,
            targets=targets,
            recently_viewed=recently_viewed,
            admitted_items=admitted_items,
            cache_limit=cache_limit,
        )

    def _admit_rows(
        self,
        accepted: list[tuple[dict[str, Any], DiscoveredContent]],
        *,
        recently_viewed: set[str],
        admitted_items: list[DiscoveredContent],
        cache_limit: int | None,
    ) -> tuple[int, int]:
        cached = 0
        rejected = 0
        for row, item in accepted:
            if (cache_limit is not None and cached >= cache_limit) or self._pool_full():
                break
            outcome = self._admit_one(
                row, item, recently_viewed=recently_viewed, admitted_items=admitted_items
            )
            if outcome == "cached":
                cached += 1
            else:
                rejected += 1
        return cached, rejected

    def _admit_share_aware(
        self,
        accepted: list[tuple[dict[str, Any], DiscoveredContent]],
        *,
        targets: dict[str, int],
        recently_viewed: set[str],
        admitted_items: list[DiscoveredContent],
        cache_limit: int | None,
    ) -> tuple[int, int]:
        """Two-round admission honoring per-family share targets.

        Round 1 admits only under-share rows (family available < target),
        incrementing the local family count as it goes. Round 2 fills any
        remaining global slots with the deferred over-share rows — availability
        beats purity (spec invariant 3). Over-share rows are *deferred*, never
        rejected, so they stay ``evaluated`` and can win a slot on a later tick
        once share-aware rebalancing (Phase 3) frees one.
        """

        available = self._available_by_family()
        cached = 0
        rejected = 0
        skipped_over_share = 0
        deferred: list[tuple[dict[str, Any], DiscoveredContent]] = []
        for row, item in accepted:
            if (cache_limit is not None and cached >= cache_limit) or self._pool_full():
                deferred.append((row, item))
                continue
            family = self._row_source_family(row, item)
            if int(available.get(family, 0)) >= int(targets.get(family, 0)):
                deferred.append((row, item))
                skipped_over_share += 1
                continue
            outcome = self._admit_one(
                row, item, recently_viewed=recently_viewed, admitted_items=admitted_items
            )
            if outcome == "cached":
                cached += 1
                available[family] = int(available.get(family, 0)) + 1
            else:
                rejected += 1
        for row, item in deferred:
            if (cache_limit is not None and cached >= cache_limit) or self._pool_full():
                break
            outcome = self._admit_one(
                row, item, recently_viewed=recently_viewed, admitted_items=admitted_items
            )
            if outcome == "cached":
                cached += 1
            else:
                rejected += 1
        if skipped_over_share:
            logger.debug(
                "admission round-1 deferred %d over-share row(s) (share-aware fairness)",
                skipped_over_share,
            )
        return cached, rejected

    def _admit_one(
        self,
        row: dict[str, Any],
        item: DiscoveredContent,
        *,
        recently_viewed: set[str],
        admitted_items: list[DiscoveredContent],
    ) -> str:
        """Attempt one row's content-cache admission.

        Returns ``"cached"`` on success or ``"rejected"`` when the row is
        recently viewed, blocked by a cache-admission guard, or the engine
        declined to persist it. Callers gate the global/limit caps first.
        """

        if self._is_recently_viewed(item, recently_viewed):
            self.database.reject_discovery_candidate(
                int(row["id"]),
                status=REJECTED_RECENTLY_VIEWED,
                reason="recently viewed",
            )
            return "rejected"
        block_status, block_reason = self._cache_admission_block(row, item)
        if block_status:
            if block_status == "temporal_review_due":
                self._requeue_temporal_review(int(row["id"]), block_reason)
                return "rejected"
            self.database.reject_discovery_candidate(
                int(row["id"]),
                status=block_status,
                reason=block_reason,
            )
            return "rejected"
        detailed_cache_fn = getattr(
            self.discovery_engine,
            "cache_evaluated_results_detailed",
            None,
        )
        if callable(detailed_cache_fn):
            cache_outcome = detailed_cache_fn([item])
            persisted = int(cache_outcome.newly_cached)
            item_outcomes = tuple(getattr(cache_outcome, "items", ()))
            temporal_rejection_reason = (
                str(getattr(item_outcomes[0], "temporal_rejection_reason", "") or "")
                if item_outcomes
                else ""
            )
            if temporal_rejection_reason:
                self.database.reject_discovery_candidate(
                    int(row["id"]),
                    status=REJECTED_TEMPORAL_STALE,
                    reason=temporal_rejection_reason,
                )
                return "rejected"
            temporal_review_reason = (
                str(getattr(item_outcomes[0], "temporal_review_reason", "") or "")
                if item_outcomes
                else ""
            )
            if temporal_review_reason:
                self._requeue_temporal_review(int(row["id"]), temporal_review_reason)
                return "rejected"
        else:
            cache_fn = getattr(self.discovery_engine, "cache_evaluated_results", None)
            if callable(cache_fn):
                persisted = int(cache_fn([item]))
            else:
                self.discovery_engine._cache_results([item])  # noqa: SLF001
                persisted = 1
        if persisted > 0:
            self.database.mark_discovery_candidate_cached(int(row["id"]))
            admitted_items.append(item)
            return "cached"
        self.database.reject_discovery_candidate(
            int(row["id"]),
            status=REJECTED_CACHE_ADMISSION,
            reason="cache admission skipped",
        )
        return "rejected"

    def _share_targets(self) -> dict[str, int] | None:
        """Return per-family visible-pool targets, or ``None`` for legacy mode."""

        provider = self.source_share_targets
        if provider is None:
            return None
        try:
            raw = provider()
        except Exception:
            logger.debug("source share targets provider failed", exc_info=True)
            return None
        if not raw:
            return None
        return {str(family): int(target) for family, target in dict(raw).items()}

    def _available_by_family(self) -> dict[str, int]:
        """Snapshot the current frontend-visible pool availability by family."""

        count_fn = getattr(self.database, "count_pool_available_candidates_by_source", None)
        if not callable(count_fn):
            return {}
        try:
            try:
                counts = count_fn(xhs_self_nickname=self._current_xhs_self_nickname())
            except TypeError:
                counts = count_fn()
        except Exception:
            logger.debug("available-by-source snapshot failed", exc_info=True)
            return {}
        return {str(family): int(count) for family, count in dict(counts).items()}

    def _under_share_platforms(self) -> list[str]:
        """Family keys currently below their share target (empty in legacy mode)."""

        targets = self._share_targets()
        if not targets:
            return []
        available = self._available_by_family()
        return [
            family
            for family, target in targets.items()
            if int(available.get(family, 0)) < int(target)
        ]

    @staticmethod
    def _row_source_family(row: dict[str, Any], item: DiscoveredContent) -> str:
        """Resolve an admission row's pool source family (invariant: use _source_family)."""

        platform = row.get("source_platform") or getattr(item, "source_platform", "")
        strategy = (
            row.get("source_strategy") or row.get("source") or getattr(item, "source_strategy", "")
        )
        return _source_family(strategy, platform)

    def _cache_admission_block(
        self,
        row: dict[str, Any],
        item: DiscoveredContent,
    ) -> tuple[str, str]:
        temporal = evaluate_temporal_eligibility(
            temporal_class=getattr(item, "temporal_class", "unknown"),
            temporal_confidence=getattr(item, "temporal_confidence", 0.0),
            published_at=getattr(item, "published_at", ""),
            temporal_validity_mode=getattr(item, "temporal_validity_mode", "none"),
            temporal_valid_until=getattr(item, "temporal_valid_until", ""),
            temporal_scope=getattr(item, "temporal_scope", "none"),
            temporal_evidence=getattr(item, "temporal_evidence", ""),
            temporal_state=getattr(item, "temporal_state", "unknown"),
            temporal_next_review_at=getattr(item, "temporal_next_review_at", ""),
            temporal_evaluated_at=getattr(item, "temporal_evaluated_at", ""),
            temporal_policy_version=getattr(item, "temporal_policy_version", "v1"),
            evidence_complete=getattr(item, "temporal_evidence_complete", False),
        )
        if temporal.hard_expired:
            return REJECTED_TEMPORAL_STALE, temporal.rejection_reason
        if temporal.needs_review:
            return "temporal_review_due", temporal.reason

        block_fn = getattr(self.discovery_engine, "cache_admission_block_reason", None)
        if not callable(block_fn):
            return "", ""
        try:
            reason = str(block_fn(item) or "").strip().lower()
        except Exception:
            logger.debug("cache admission block check failed", exc_info=True)
            return "", ""
        if reason == "recently_viewed":
            return REJECTED_RECENTLY_VIEWED, "recently viewed"
        if reason == "franchise_quota":
            franchise = str(item.franchise_key or row.get("franchise_key") or "").strip()
            suffix = f": {franchise}" if franchise else ""
            return REJECTED_FRANCHISE_QUOTA, f"franchise quota reached{suffix}"
        if reason == "temporal_stale":
            return REJECTED_TEMPORAL_STALE, "temporal stale"
        if reason == "temporal_review_due":
            return "temporal_review_due", "temporal review due"
        return "", ""

    def _requeue_temporal_review(self, candidate_id: int, reason: str) -> None:
        """Best-effort compatibility bridge for a review-due candidate."""

        requeue = getattr(
            self.database,
            "requeue_discovery_candidate_for_temporal_review",
            None,
        )
        if callable(requeue):
            requeue(candidate_id, reason)
            return
        reset = getattr(self.database, "reset_discovery_candidates_to_pending", None)
        if callable(reset):
            try:
                reset([candidate_id], reason=reason, increment_attempts=False)
            except TypeError:
                reset([candidate_id], reason=reason)

    def _threshold_for(self, row: dict[str, Any]) -> float:
        payload = self._raw_payload(row)
        candidate_threshold = self._coerce_threshold(row.get("score_threshold"))
        if candidate_threshold is None:
            candidate_threshold = self._coerce_threshold(payload.get("score_threshold"))
        return effective_admission_threshold(
            self._strategy_for(row, payload),
            self._normalized_admission_min_score(),
            candidate_threshold,
        )

    @staticmethod
    def _strategy_for(row: dict[str, Any], payload: dict[str, Any]) -> str:
        strategy = row.get("source_strategy") or payload.get("source_strategy") or ""
        return str(strategy).strip().lower()

    def _max_pending_per_source(self) -> int | None:
        return discovery_candidate_pending_cap(int(self.pool_target_count))

    def _filter_known_writes(
        self,
        writes: list[DiscoveryCandidateWrite],
    ) -> tuple[list[DiscoveryCandidateWrite], dict[str, int], set[int]]:
        diagnostics = {
            "input": len(writes),
            "kept": 0,
            "duplicate_in_batch": 0,
            "known_candidate": 0,
            "known_cache": 0,
        }
        if not writes:
            return [], diagnostics, set()

        candidate_keys = [write.candidate_key for write in writes if write.candidate_key]
        known_candidate_keys = self._existing_candidate_keys(candidate_keys)
        canonical_cache_lookup = callable(
            getattr(self.database, "get_existing_content_cache_item_keys", None)
        )
        known_cache_keys = self._existing_content_cache_item_keys(candidate_keys)
        known_cache_ids: set[str] = set()
        if not canonical_cache_lookup:
            content_ids = [
                value
                for write in writes
                for value in (write.bvid, write.content_id)
                if str(value or "").strip()
            ]
            known_cache_ids = self._existing_content_cache_ids(content_ids)

        seen: set[str] = set()
        kept: list[DiscoveryCandidateWrite] = []
        keyword_totals: dict[int, int] = {}
        keyword_novel: set[int] = set()
        for write in writes:
            try:
                keyword_id = int(write.source_keyword_id or 0)
            except (TypeError, ValueError):
                keyword_id = 0
            if keyword_id > 0:
                keyword_totals[keyword_id] = keyword_totals.get(keyword_id, 0) + 1
            key = str(write.candidate_key or "").strip()
            if not key:
                continue
            if key in seen:
                diagnostics["duplicate_in_batch"] += 1
                continue
            seen.add(key)
            if key in known_candidate_keys:
                diagnostics["known_candidate"] += 1
                continue
            identifiers = {
                str(value or "").strip()
                for value in (write.bvid, write.content_id)
                if str(value or "").strip()
            }
            if key in known_cache_keys or (
                not canonical_cache_lookup and identifiers & known_cache_ids
            ):
                diagnostics["known_cache"] += 1
                continue
            kept.append(write)
            if keyword_id > 0:
                keyword_novel.add(keyword_id)
        diagnostics["kept"] = len(kept)
        duplicate_only_keyword_ids = set(keyword_totals) - keyword_novel
        return kept, diagnostics, duplicate_only_keyword_ids

    def _existing_candidate_keys(self, candidate_keys: list[str]) -> set[str]:
        getter = getattr(self.database, "get_existing_discovery_candidate_keys", None)
        if not callable(getter) or not candidate_keys:
            return set()
        try:
            return {str(key) for key in getter(candidate_keys)}
        except Exception:
            logger.debug("existing discovery candidate key lookup failed", exc_info=True)
            return set()

    def _existing_content_cache_ids(self, content_ids: list[str]) -> set[str]:
        getter = getattr(self.database, "get_existing_content_cache_ids", None)
        if not callable(getter) or not content_ids:
            return set()
        try:
            return {str(key) for key in getter(content_ids)}
        except Exception:
            logger.debug("existing content-cache id lookup failed", exc_info=True)
            return set()

    def _existing_content_cache_item_keys(self, item_keys: list[str]) -> set[str]:
        getter = getattr(self.database, "get_existing_content_cache_item_keys", None)
        if not callable(getter) or not item_keys:
            return set()
        try:
            return {str(key) for key in getter(item_keys)}
        except Exception:
            logger.debug("existing content-cache item-key lookup failed", exc_info=True)
            return set()

    def _eval_supply_counts(self) -> tuple[int, int]:
        count_fn = getattr(self.database, "count_discovery_candidates_by_status", None)
        if not callable(count_fn):
            return 0, 0
        try:
            counts = dict(count_fn())
        except Exception:
            logger.debug("discovery candidate supply count unavailable", exc_info=True)
            return 0, 0
        return (
            int(counts.get("pending_eval_ready", counts.get("pending_eval", 0)) or 0),
            int(counts.get("evaluating", 0) or 0),
        )

    def _effective_batch_size(self, batch_size: int) -> int:
        requested = max(0, int(batch_size))
        if requested <= 0:
            return 0
        try:
            min_batch = max(1, int(self.min_eval_batch_size))
        except (TypeError, ValueError):
            min_batch = 1
        requested = max(requested, min_batch)
        hard_cap = int(getattr(self.discovery_engine, "_EVALUATE_BATCH_HARD_CAP", 0) or 0)
        if hard_cap > 0:
            return min(requested, hard_cap)
        return requested

    def _oversampled_produce_limit(self, limit: int) -> int:
        requested = max(0, int(limit))
        if requested <= 0:
            return 0
        try:
            factor = int(self.candidate_fetch_oversample)
        except (TypeError, ValueError):
            factor = 1
        if factor <= 1:
            return requested
        return min(requested * factor, max(requested, 120))

    def _oversampled_strategy_limits(
        self,
        strategy_limits: dict[str, int] | None,
        *,
        requested_limit: int,
        produce_limit: int,
    ) -> dict[str, int] | None:
        if not strategy_limits or produce_limit <= requested_limit:
            return strategy_limits
        requested = max(1, int(requested_limit))
        scaled: dict[str, int] = {}
        for strategy, raw_value in strategy_limits.items():
            value = max(0, int(raw_value))
            if value <= 0:
                scaled[strategy] = 0
                continue
            scaled[strategy] = max(value, (value * produce_limit + requested - 1) // requested)
        return scaled

    def _waiting_pending_eval_count(self, batch_size: int) -> int | None:
        min_batch = min(max(1, int(self.min_eval_batch_size)), max(1, int(batch_size)))
        if min_batch <= 1:
            self._first_pending_eval_seen_at = None
            return None

        pending_count = self._pending_eval_count()
        if pending_count is None:
            return None
        if pending_count <= 0:
            self._first_pending_eval_seen_at = None
            return None
        if pending_count >= min_batch:
            self._first_pending_eval_seen_at = None
            return None

        now = float(self.time_fn())
        first_seen = self._first_pending_eval_seen_at
        if first_seen is None:
            self._first_pending_eval_seen_at = now
            first_seen = now
        max_wait = max(0.0, float(self.max_eval_wait_seconds or 0.0))
        waited = max(0.0, now - first_seen)
        if max_wait > 0 and waited >= max_wait:
            self._first_pending_eval_seen_at = None
            return None
        if max_wait <= 0:
            return None

        logger.info(
            "candidate eval drain waiting: pending=%s min_batch=%s waited=%.1fs max_wait=%.1fs",
            pending_count,
            min_batch,
            waited,
            max_wait,
        )
        return pending_count

    def _pending_eval_count(self) -> int | None:
        count_fn = getattr(self.database, "count_discovery_candidates_by_status", None)
        if not callable(count_fn):
            return None
        try:
            counts = dict(count_fn())
        except Exception:
            logger.debug("pending discovery candidate count unavailable", exc_info=True)
            return None
        return int(counts.get("pending_eval_ready", counts.get("pending_eval", 0)) or 0)

    @staticmethod
    def _raw_payload(row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("raw_payload") or {}
        if isinstance(payload, dict):
            return dict(payload)
        if not isinstance(payload, str) or not payload.strip():
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}

    @staticmethod
    def _coerce_threshold(value: object) -> float | None:
        if not isinstance(value, int | float | str):
            return None
        try:
            threshold = float(value)
        except (TypeError, ValueError):
            return None
        if threshold <= 0:
            return None
        return min(1.0, threshold)

    def _normalized_admission_min_score(self) -> float:
        try:
            threshold = float(self.admission_min_score)
        except (TypeError, ValueError):
            return 0.60
        if threshold <= 0.0 or threshold > 1.0:
            return 0.60
        return threshold

    def _current_xhs_self_nickname(self) -> str:
        if self.xhs_self_nickname_provider is not None:
            try:
                return str(self.xhs_self_nickname_provider() or "").strip()
            except Exception:
                logger.debug("xhs_self_nickname_provider failed", exc_info=True)
        return str(self.xhs_self_nickname or "").strip()

    def _recent_viewed_content_keys(self) -> set[str]:
        get_recent = getattr(self.database, "get_seen_content_keys", None)
        if not callable(get_recent):
            get_recent = getattr(self.database, "get_recent_viewed_content_keys", None)
        if not callable(get_recent):
            get_recent = getattr(self.database, "get_seen_bvids", None)
        if not callable(get_recent):
            get_recent = getattr(self.database, "get_recent_viewed_bvids", None)
        if not callable(get_recent):
            return set()
        try:
            return {str(item).strip() for item in get_recent() if str(item).strip()}
        except Exception:
            logger.debug("recent viewed content keys unavailable", exc_info=True)
            return set()

    def _candidate_view_keys(self, item: DiscoveredContent) -> set[str]:
        view_key_fn = getattr(self.discovery_engine, "_candidate_view_keys", None)
        if callable(view_key_fn):
            try:
                return {str(value).strip() for value in view_key_fn(item) if str(value).strip()}
            except Exception:
                logger.debug("discovery candidate view-key conversion failed", exc_info=True)
        keys: set[str] = set()
        platform = str(item.source_platform or ("bilibili" if item.bvid else "")).strip().lower()
        for value in {item.bvid, item.content_id}:
            content_id = str(value or "").strip()
            if not content_id:
                continue
            keys.add(content_id)
            if platform:
                keys.add(f"{platform}:{content_id}")
        return keys

    def _is_recently_viewed(self, item: DiscoveredContent, recently_viewed: set[str]) -> bool:
        return bool(recently_viewed) and not self._candidate_view_keys(item).isdisjoint(
            recently_viewed
        )

    def _pool_available_count(self) -> int:
        count_fn = getattr(self.database, "count_pool_candidates", None)
        if not callable(count_fn):
            return 0
        try:
            return int(count_fn(xhs_self_nickname=self._current_xhs_self_nickname()))
        except TypeError:
            return int(count_fn())

    def _pool_full(self) -> bool:
        if self.pool_target_count <= 0:
            return False
        return self._pool_available_count() >= int(self.pool_target_count)
