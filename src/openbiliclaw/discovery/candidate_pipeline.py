"""Shared evaluator/admission pipeline for discovery candidates."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
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


def _default_score_thresholds() -> dict[str, float]:
    return {
        "search": 0.65,
        "trending": 0.60,
        "hot": 0.60,
        "related": 0.65,
        "related_chain": 0.65,
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
    xhs_self_nickname: str = ""
    xhs_self_nickname_provider: Callable[[], str] | None = None
    max_eval_attempts: int = 5
    max_batch_eval_attempts: int = 50
    _drain_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
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
        enqueue = self.database.enqueue_discovery_candidates
        cap = self._max_pending_per_source()
        if cap is None:
            return int(enqueue(writes))
        try:
            return int(enqueue(writes, max_pending_per_source=cap))
        except TypeError:
            return int(enqueue(writes))

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

        produce_fn = getattr(self.discovery_engine, "produce_candidates", None)
        if callable(produce_fn):
            items = await produce_fn(
                profile,
                strategies=strategies,
                limit=limit,
                strategy_limits=strategy_limits,
                pool_snapshot=pool_snapshot,
                **extra,
            )
        else:
            items = await self.discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=limit,
                strategy_limits=strategy_limits,
                pool_snapshot=pool_snapshot,
                **extra,
            )
        return self.enqueue_candidates(list(items), source_context="mixed")

    async def drain_pending(self, *, profile: Any, batch_size: int = 30) -> dict[str, int]:
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
        batch_size: int = 30,
    ) -> dict[str, int]:
        """Evaluate one pending batch while the shared drain lock is held."""

        self.last_admitted_items = []
        batch_size = self._effective_batch_size(batch_size)
        if batch_size <= 0:
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        if self._pool_full():
            return {"evaluated": 0, "cached": 0, "rejected": 0}

        recently_viewed = self._recent_viewed_content_keys()
        admitted_items: list[DiscoveredContent] = []
        retry_cached, retry_rejected = self._admit_evaluated_candidates(
            limit=batch_size,
            recently_viewed=recently_viewed,
            admitted_items=admitted_items,
        )
        if self._pool_full():
            self.last_admitted_items = list(admitted_items)
            return {"evaluated": 0, "cached": retry_cached, "rejected": retry_rejected}

        rows = self.database.claim_discovery_candidates_for_eval(limit=batch_size)
        if not rows:
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
        admission_policy = str(payload.get("admission_policy") or "").strip().lower()
        if admission_policy in {"observed", "always_admit"}:
            return 0.0
        candidate_threshold = self._coerce_threshold(row.get("score_threshold"))
        if candidate_threshold is None:
            candidate_threshold = self._coerce_threshold(payload.get("score_threshold"))
        if candidate_threshold is not None:
            return candidate_threshold
        strategy = str(row.get("source_strategy") or "").strip().lower()
        if "related" in strategy:
            return self.score_thresholds["related"]
        if "search" in strategy:
            return self.score_thresholds["search"]
        if "trending" in strategy:
            return self.score_thresholds["trending"]
        if "hot" in strategy:
            return self.score_thresholds["hot"]
        if "explore" in strategy:
            return self.score_thresholds["explore"]
        if "feed" in strategy:
            return self.score_thresholds["feed"]
        return self.score_thresholds["default"]

    def _max_pending_per_source(self) -> int | None:
        return discovery_candidate_pending_cap(int(self.pool_target_count))

    def _effective_batch_size(self, batch_size: int) -> int:
        requested = max(0, int(batch_size))
        if requested <= 0:
            return 0
        hard_cap = int(getattr(self.discovery_engine, "_EVALUATE_BATCH_HARD_CAP", 0) or 0)
        if hard_cap > 0:
            return min(requested, hard_cap)
        return requested

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
