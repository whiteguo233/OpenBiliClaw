"""Shared evaluator/admission pipeline for discovery candidates."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.candidate_pool import (
    REJECTED_CACHE_ADMISSION,
    REJECTED_FRANCHISE_QUOTA,
    REJECTED_LOW_SCORE,
    REJECTED_RECENTLY_VIEWED,
    DiscoveryCandidateWrite,
    discovered_content_to_candidate_write,
    discovery_candidate_pending_cap,
    row_to_discovered_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent

logger = logging.getLogger(__name__)
_DEFAULT_EVAL_BATCH_SIZE = 45


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
        writes, diagnostics = self._filter_known_writes(writes)
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
        if not writes:
            return 0
        enqueue = self.database.enqueue_discovery_candidates
        cap = self._max_pending_per_source()
        if cap is None:
            return int(enqueue(writes))
        try:
            return int(enqueue(writes, max_pending_per_source=cap))
        except TypeError:
            return int(enqueue(writes))

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
    ) -> dict[str, int]:
        """Evaluate one pending batch and admit accepted items into content_cache."""

        if self._drain_lock.locked():
            self.last_admitted_items = []
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        async with self._drain_lock:
            return await self._drain_pending_locked(profile=profile, batch_size=batch_size)

    async def _drain_pending_locked(
        self,
        *,
        profile: Any,
        batch_size: int = _DEFAULT_EVAL_BATCH_SIZE,
    ) -> dict[str, int]:
        """Evaluate one pending batch while the shared drain lock is held."""

        self.last_admitted_items = []
        batch_size = self._effective_batch_size(batch_size)
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

        waiting_pending = self._waiting_pending_eval_count(batch_size)
        if waiting_pending is not None:
            self.last_admitted_items = list(admitted_items)
            return {
                "evaluated": 0,
                "cached": retry_cached,
                "rejected": retry_rejected,
                "waiting": waiting_pending,
            }

        rows = self.database.claim_discovery_candidates_for_eval(limit=claim_limit)
        if not rows:
            self._first_pending_eval_seen_at = None
            self.last_admitted_items = list(admitted_items)
            return {"evaluated": 0, "cached": retry_cached, "rejected": retry_rejected}

        items = [row_to_discovered_content(row) for row in rows]
        try:
            scores = await self.discovery_engine.evaluate_content_batch(
                items,
                profile,
                source_context="mixed",
                batch_size=batch_size,
            )
        except asyncio.CancelledError:
            logger.info("discovery candidate batch evaluation cancelled; releasing claims")
            self._release_eval_claims(rows, reason="evaluation cancelled", increment_attempts=False)
            self.last_admitted_items = list(admitted_items)
            raise
        except Exception as exc:
            logger.exception("discovery candidate batch evaluation failed")
            self._release_eval_claims(rows, reason=str(exc), increment_attempts=False)
            self.last_admitted_items = list(admitted_items)
            return {
                "evaluated": 0,
                "cached": retry_cached,
                "rejected": retry_rejected,
                "failed": len(rows),
            }

        if len(scores) != len(items):
            reason = f"evaluation returned {len(scores)} scores for {len(items)} candidates"
            logger.warning("discovery candidate batch evaluation incomplete: %s", reason)
            self._release_eval_claims(rows, reason=reason, increment_attempts=False)
            self.last_admitted_items = list(admitted_items)
            return {
                "evaluated": 0,
                "cached": retry_cached,
                "rejected": retry_rejected,
                "failed": len(rows),
            }

        try:
            await self._normalize_evaluated_items(items)
            accepted: list[tuple[dict[str, Any], DiscoveredContent]] = []
            rejected = 0
            for row, item, score in zip(rows, items, scores, strict=True):
                final_score = float(item.relevance_score or score or 0.0)
                if self._is_recently_viewed(item, recently_viewed):
                    rejected += 1
                    continue
                if final_score < self._threshold_for(row):
                    rejected += 1
                    continue
                accepted.append((row, item))
            self._persist_evaluations(rows, items, scores, recently_viewed=recently_viewed)
        except asyncio.CancelledError:
            logger.info("discovery candidate post-evaluation cancelled; releasing claims")
            self._release_eval_claims(
                rows,
                reason="post-evaluation cancelled",
                increment_attempts=False,
            )
            self.last_admitted_items = list(admitted_items)
            raise
        except Exception as exc:
            logger.exception("discovery candidate post-evaluation processing failed")
            self._release_eval_claims(rows, reason=str(exc), increment_attempts=False)
            self.last_admitted_items = list(admitted_items)
            return {
                "evaluated": 0,
                "cached": retry_cached,
                "rejected": retry_rejected,
                "failed": len(rows),
            }
        cached, admission_rejected = self._admit_until_full(
            accepted,
            recently_viewed=recently_viewed,
            admitted_items=admitted_items,
        )
        self.last_admitted_items = list(admitted_items)
        return {
            "evaluated": len(rows),
            "cached": retry_cached + cached,
            "rejected": retry_rejected + rejected + admission_rejected,
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
        try:
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
    ) -> None:
        evaluations: list[dict[str, Any]] = []
        for row, item, score in zip(rows, items, scores, strict=True):
            final_score = float(item.relevance_score or score or 0.0)
            status = "evaluated"
            eval_error = ""
            if self._is_recently_viewed(item, recently_viewed):
                status = REJECTED_RECENTLY_VIEWED
                eval_error = "recently viewed"
            elif final_score < self._threshold_for(row):
                status = REJECTED_LOW_SCORE
                eval_error = f"score {final_score:.2f} below threshold"
            evaluations.append(
                {
                    "candidate_id": int(row["id"]),
                    "status": status,
                    "relevance_score": final_score,
                    "relevance_reason": item.relevance_reason,
                    "topic_key": item.topic_key,
                    "topic_group": item.topic_group,
                    "style_key": item.style_key,
                    "franchise_key": item.franchise_key,
                    "pool_expression": item.pool_expression,
                    "pool_topic_label": item.pool_topic_label,
                    "eval_error": eval_error,
                }
            )
        self.database.update_discovery_candidate_evaluations(evaluations)

    def _admit_until_full(
        self,
        accepted: list[tuple[dict[str, Any], DiscoveredContent]],
        *,
        recently_viewed: set[str],
        admitted_items: list[DiscoveredContent],
    ) -> tuple[int, int]:
        cached = 0
        rejected = 0
        for row, item in accepted:
            if self._pool_full():
                break
            if self._is_recently_viewed(item, recently_viewed):
                self.database.reject_discovery_candidate(
                    int(row["id"]),
                    status=REJECTED_RECENTLY_VIEWED,
                    reason="recently viewed",
                )
                rejected += 1
                continue
            block_status, block_reason = self._cache_admission_block(row, item)
            if block_status:
                self.database.reject_discovery_candidate(
                    int(row["id"]),
                    status=block_status,
                    reason=block_reason,
                )
                rejected += 1
                continue
            cache_fn = getattr(self.discovery_engine, "cache_evaluated_results", None)
            if callable(cache_fn):
                persisted = int(cache_fn([item]))
            else:
                self.discovery_engine._cache_results([item])  # noqa: SLF001
                persisted = 1
            if persisted > 0:
                self.database.mark_discovery_candidate_cached(int(row["id"]))
                admitted_items.append(item)
                cached += 1
            else:
                self.database.reject_discovery_candidate(
                    int(row["id"]),
                    status=REJECTED_CACHE_ADMISSION,
                    reason="cache admission skipped",
                )
                rejected += 1
        return cached, rejected

    def _cache_admission_block(
        self,
        row: dict[str, Any],
        item: DiscoveredContent,
    ) -> tuple[str, str]:
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
        return "", ""

    def _threshold_for(self, row: dict[str, Any]) -> float:
        payload = self._raw_payload(row)
        candidate_threshold = self._coerce_threshold(row.get("score_threshold"))
        if candidate_threshold is None:
            candidate_threshold = self._coerce_threshold(payload.get("score_threshold"))
        if candidate_threshold is not None:
            return candidate_threshold
        return self._normalized_admission_min_score()

    def _max_pending_per_source(self) -> int | None:
        return discovery_candidate_pending_cap(int(self.pool_target_count))

    def _filter_known_writes(
        self,
        writes: list[DiscoveryCandidateWrite],
    ) -> tuple[list[DiscoveryCandidateWrite], dict[str, int]]:
        diagnostics = {
            "input": len(writes),
            "kept": 0,
            "duplicate_in_batch": 0,
            "known_candidate": 0,
            "known_cache": 0,
        }
        if not writes:
            return [], diagnostics

        candidate_keys = [write.candidate_key for write in writes if write.candidate_key]
        known_candidate_keys = self._existing_candidate_keys(candidate_keys)
        content_ids = [
            value
            for write in writes
            for value in (write.bvid, write.content_id)
            if str(value or "").strip()
        ]
        known_cache_ids = self._existing_content_cache_ids(content_ids)

        seen: set[str] = set()
        kept: list[DiscoveryCandidateWrite] = []
        for write in writes:
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
            if identifiers & known_cache_ids:
                diagnostics["known_cache"] += 1
                continue
            kept.append(write)
        diagnostics["kept"] = len(kept)
        return kept, diagnostics

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
            int(counts.get("pending_eval", 0) or 0),
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
        return int(counts.get("pending_eval", 0) or 0)

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
        get_recent = getattr(self.database, "get_recent_viewed_content_keys", None)
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
